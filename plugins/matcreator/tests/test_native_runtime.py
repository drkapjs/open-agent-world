"""Worker/protocol tests and opt-in Pack engine + real ADK integration.

OAW_MATCREATOR_MANAGED=1 uses dependencies prepared by normal Pack setup.
No external MatCreator package, model, credential or scientific service is used.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from open_agent_world.plugin_api import AgentConfig, RuntimeInput, RuntimeModelConnection, ScopedToolDefinition
from plugins.matcreator.oaw_matcreator.native_runtime import MatCreatorRuntime
from plugins.matcreator.oaw_matcreator.native_adapter import read_agent_graph


class Provider:
    def __init__(self, root, url='http://127.0.0.1:1/v1'):
        self.root, self.url = root, url
        self.calls = []
        self.projections = []
        self.available = True

    def resolve_model(self, model):
        return RuntimeModelConnection('native-test', 'openai', self.url, 'test-secret')

    def runtime_state_directory(self, agent):
        return self.root / agent

    async def list_tools(self, agent):
        return [ScopedToolDefinition('echo', 'echo', 'Verify a bridge call', input_schema={
            'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value']})] if self.available else []

    async def invoke_tool(self, agent, capability, arguments):
        self.calls.append((agent, capability, arguments))
        return {'verified': arguments['value']}

    async def publish_execution(self, agent, run, snapshot):
        from open_agent_world.plugin_api import ExecutionSnapshot
        self.projection = ExecutionSnapshot.model_validate(snapshot)
        self.projections.append((run, self.projection))

    async def pending_conversation_input(self, agent, run):
        return []

    async def acknowledge_conversation_input(self, agent, run, message_ids):
        pass


@pytest.mark.asyncio
async def test_native_models_receive_followups_after_tool_results_and_share_them_with_children(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1]))
    from oaw_matcreator_engine.resources import ResourceBridge
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types
    pending = [{'id': 'update', 'text': 'You: skip c', 'sequence': 2}]
    acknowledged = []

    async def rpc(method, **arguments):
        if method == 'pending_conversation_input':
            return list(pending)
        assert method == 'acknowledge_conversation_input'
        acknowledged.extend(arguments['message_ids'])
        pending.clear()
        return {'ok': True}

    bridge = ResourceBridge(rpc, lambda *args: None)
    request = LlmRequest(contents=[types.Content(role='user', parts=[
        types.Part.from_function_response(name='b', response={'result': 'b returned'})])])
    await bridge.before_model(None, request)
    assert request.contents[0].parts[0].function_response.name == 'b'
    assert request.contents[1].parts[0].text == 'You: skip c'
    child = LlmRequest(contents=[])
    await bridge.before_model(None, child)
    assert child.contents[0].parts[0].text == 'You: skip c'
    assert acknowledged == ['update']


def context(run='run1', session='session-a', agent='agent-a'):
    return SimpleNamespace(run_id=run, context_id=session, agent_id=agent)


def test_graph_reader_distinguishes_partial_writes_from_an_empty_graph(tmp_path):
    path = tmp_path / 'native.json'
    assert read_agent_graph(path) is None
    path.write_text('{"nodes":', encoding='utf-8')
    assert read_agent_graph(path) is None
    path.write_text(json.dumps({'nodes': {'child': {
        'id': 'child', 'status': 'success', 'parent_id': 'root', 'tool_calls': ['private trace'],
    }}}), encoding='utf-8')
    nodes = read_agent_graph(path)
    assert nodes[0]['id'] == 'child' and nodes[0]['status'] == 'success'
    assert 'tool_calls' not in nodes[0]
    path.write_text('{"nodes": {}}', encoding='utf-8')
    assert read_agent_graph(path) == []


@pytest.mark.asyncio
async def test_bridge_resolves_live_tools_after_revocation(tmp_path):
    provider = Provider(tmp_path)
    runtime = MatCreatorRuntime(provider)
    definitions = await runtime._bridge('a', {'method': 'list_tools'})
    assert definitions[0]['capability_id'] == 'echo'
    provider.available = False
    result = await runtime._bridge('a', {'method': 'invoke_tool', 'capability_id': 'echo', 'arguments': {}})
    assert result['ok'] is False
    assert provider.calls == []


def test_old_external_paths_are_migrated_out_of_card_configuration():
    from plugins.matcreator.oaw_matcreator.native_card import NativeConfig
    value = NativeConfig.model_validate({'source_directory': 'D:/old/checkout',
        'python_executable': 'D:/old/python.exe', 'agent_mode': 'flash'})
    assert value.agent_mode == 'flash'
    assert 'source_directory' not in value.model_dump()
    assert 'python_executable' not in value.model_json_schema()['properties']


@pytest.mark.asyncio
async def test_sent_message_bridge_returns_receipt_without_echoing_answer(tmp_path):
    class SendingProvider(Provider):
        async def list_tools(self, agent):
            return [ScopedToolDefinition('send', 'send_conversation_message', 'Send', input_schema={})]

        async def invoke_tool(self, agent, capability, arguments):
            return {'id': 'sent-id', 'content': 'long user-visible answer'}

    result = await MatCreatorRuntime(SendingProvider(tmp_path))._bridge('a', {
        'method': 'invoke_tool', 'capability_id': 'send', 'arguments': {}})
    assert result['ok']
    assert 'sent-id' in str(result) and 'Do not repeat' in str(result)
    assert 'long user-visible answer' not in str(result)


@pytest.fixture
def scripted_model(request):
    """A deterministic OpenAI endpoint; the actual Pack agents choose tools."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    calls = []
    planner_round = 0
    executor_round = 0
    scenario = getattr(request, 'param', False)
    inject_errors = scenario is True
    injected = set()

    def function(name, arguments):
        return {'role': 'assistant', 'content': None, 'tool_calls': [{
            'id': 'call_' + str(len(calls)), 'type': 'function',
            'function': {'name': name, 'arguments': json.dumps(arguments)}}]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal planner_round, executor_round
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(request)
            names = {t['function']['name'] for t in request.get('tools', [])}
            original = next((m['content'] for m in request['messages'] if m['role'] == 'user'), '')
            layer = ('planner' if 'run_flash_step' in names else 'executor' if 'run_node_executor' in names
                else ('recursive' if 'child bridge check' in str(original) else 'step') if 'run_sub_agent' in names else None)
            if any('Create a compact continuation checkpoint' in str(m.get('content', ''))
                   for m in request['messages'] if m['role'] == 'system'):
                reply = {'role': 'assistant', 'content': 'Goal: verify the OAW bridge. The plan is ready; retain the current user approval and inspect tool results.'}
            elif layer == 'planner' and 'Only inspect context pressure' in str(request['messages']):
                reply = {'role': 'assistant', 'content': 'Context inspected; awaiting approval.'}
            elif names == {'submit_step_result'}:
                reply = function('submit_step_result', {'status': 'success', 'key_results': 'Existing echo evidence',
                    'concise_summary': 'Reported existing evidence after bounded repair'})
            elif scenario == 'repair' and layer == 'step':
                results = [m for m in request['messages'] if m['role'] == 'tool']
                reply = (function('oaw_invoke_tool', {'capability_id': 'echo', 'arguments': {'value': 'once'}})
                    if not results else {'role': 'assistant', 'content': 'Ended without a structured result.'})
            elif scenario == 'jobs' and layer == 'step':
                results = [m for m in request['messages'] if m['role'] == 'tool']
                resumed = 'OAW JOBS ALREADY SUBMITTED' in str(original)
                if not results:
                    reply = function('oaw_invoke_tool', {'capability_id': 'host:inspect_background_jobs' if resumed else 'host:submit_background_job',
                        'arguments': {'job_ids': ['job-test']} if resumed else {'provider': 'test.compute', 'sandbox_id': 'sandbox', 'request_key': 'md', 'specification': {}}})
                elif not any('Step result submitted successfully' in str(m.get('content')) for m in results):
                    reply = function('submit_step_result', {'status': 'success' if resumed else 'waiting',
                        'key_results': 'Observed completed output' if resumed else 'Submitted durable job',
                        'concise_summary': 'Existing job verified' if resumed else 'Waiting for external compute',
                        'job_ids': [] if resumed else ['job-test']})
                else:
                    reply = {'role': 'assistant', 'content': 'Step finished.'}
            elif scenario == 'jobs' and layer == 'executor':
                executor_round += 1
                reply = (function('run_node_executor', {'node_id': 'step_check', 'action': 'Verify the connected tool',
                    'suggested_skills': [], 'workspace_dir': ''}) if executor_round in (1, 2)
                    else {'role': 'assistant', 'content': 'Waiting for external work.' if executor_round == 2 else 'Collected existing output.'})
            elif inject_errors and layer and layer not in injected:
                injected.add(layer)
                reply = {'role': 'assistant', 'content': None, 'tool_calls': [
                    {'id': f'fault_{layer}_{i}', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}
                    for i, (name, args) in enumerate([
                        ('knowledge_search', {}), ('knowledge_search', {}), ('operation:inspect_sandbox', {}),
                        ('oaw_invoke_tool', {'capability_id': 'echo', 'arguments': {'value': f'{layer}-first'}}),
                        ('oaw_invoke_tool', {'capability_id': 'echo', 'arguments': {'value': f'{layer}-second'}}),
                        ('oaw_invoke_tool', {'capability_id': 'echo', 'arguments': ['wrong-type']}),
                    ])]}
            elif 'run_flash_step' in names:
                planner_round += 1
                if planner_round == 1:
                    reply = function('validate_graph', {'graph': {'nodes': {'step_check': {
                        'node_id': 'step_check', 'label': 'Verify bridge', 'action': 'Verify the connected tool', 'suggested_skills': []}},
                        'edges': [], 'additional_notes': 'Local deterministic integration test'}})
                elif planner_round == 2:
                    reply = {'role': 'assistant', 'content': 'Plan ready. Please approve.'}
                elif planner_round == 3:
                    reply = function('confirm_plan_and_start_execution', {})
                else:
                    reply = {'role': 'assistant', 'content': 'Verified native subagents completed.'}
            elif 'run_node_executor' in names:
                executor_round += 1
                if executor_round == 1:
                    reply = function('run_node_executor', {'node_id': 'step_check', 'action': 'Verify the connected tool',
                        'suggested_skills': [], 'workspace_dir': ''})
                elif executor_round == 2:
                    reply = function('set_node_status', {'node_id': 'step_check', 'status': 'success', 'result': 'Bridge verified'})
                else:
                    reply = {'role': 'assistant', 'content': 'Execution finished.'}
            elif 'run_sub_agent' in names:
                messages = request['messages']
                original = next(m['content'] for m in messages if m['role'] == 'user')
                is_child = 'child bridge check' in str(original)
                results = [m for m in messages if m['role'] == 'tool' and not m.get('tool_call_id', '').startswith('fault_')]
                if not results and not is_child:
                    reply = function('run_sub_agent', {'step_number': 1, 'action': 'child bridge check', 'suggested_skills': []})
                elif not results:
                    reply = function('oaw_invoke_tool', {'capability_id': 'echo', 'arguments': {'value': 'native-child'}})
                elif is_child and not any('oaw_artifact_ref' in str(m.get('content')) for m in results):
                    reply = function('oaw_invoke_tool', {'capability_id': 'publish',
                        'arguments': {'collection': 'outputs', 'path': '/workspace/result.txt'}})
                elif is_child and not any('Publish output via OAW' in str(m.get('content')) for m in results):
                    # The original local-files contract must not accept Sandbox
                    # paths as evidence, even when the model claims success.
                    reply = function('submit_step_result', {'status': 'success', 'key_results': 'Output',
                        'concise_summary': 'Check invalid reference', 'artifacts': ['/workspace/result.txt']})
                elif not any('Step result submitted successfully' in str(m.get('content')) for m in results):
                    reply = function('submit_step_result', {'status': 'success', 'key_results': 'Bridge returned evidence',
                        'concise_summary': 'Verified bridge', 'artifacts': ['oaw:artifact:outputs:v1'] if is_child else []})
                else:
                    reply = {'role': 'assistant', 'content': 'Step complete.'}
            else:
                reply = {'role': 'assistant', 'content': 'Conversation summary.'}
            base = {'id': 'chatcmpl-test', 'model': 'native-test', 'created': 1}
            if request.get('stream'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                delta = dict(reply)
                for index, tool_call in enumerate(delta.get('tool_calls') or []):
                    tool_call['index'] = index
                first = {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}
                last = {**base, 'object': 'chat.completion.chunk', 'choices': [{'index': 0, 'delta': {},
                    'finish_reason': 'tool_calls' if reply.get('tool_calls') else 'stop'}]}
                for item in (first, last):
                    self.wfile.write(('data: ' + json.dumps(item) + '\n\n').encode())
                self.wfile.write(b'data: [DONE]\n\n')
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({**base, 'object': 'chat.completion',
                    'choices': [{'index': 0, 'message': reply, 'finish_reason': 'tool_calls' if reply.get('tool_calls') else 'stop'}]}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1', calls
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_MANAGED'), reason='requires Pack orchestration dependencies')
@pytest.mark.parametrize('scripted_model', ['jobs'], indirect=True)
async def test_native_wait_releases_worker_and_restores_existing_approved_plan(tmp_path, scripted_model):
    url, calls = scripted_model
    class JobsProvider(Provider):
        job = None
        async def list_tools(self, agent):
            return [ScopedToolDefinition('host:' + name, name, 'Durable jobs', input_schema={'type': 'object'})
                for name in ('submit_background_job', 'inspect_background_jobs', 'wait_background_jobs')]
        async def invoke_tool(self, agent, capability, arguments):
            self.calls.append((agent, capability, arguments))
            if capability == 'host:submit_background_job':
                assert self.job is None
                self.job = {'id': 'job-test', 'status': 'running', 'paused': False, 'request_key': arguments['request_key']}
                return self.job
            if capability == 'host:inspect_background_jobs':
                return {'jobs': [self.job] if self.job else []}
            return {'status': 'waiting', 'job_ids': ['job-test'], 'durable': True}
    provider = JobsProvider(tmp_path, url)
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig('agent-a', 'Native')
    await runtime.create_agent(config)
    async def turn(run, text, notification=None):
        scope = context(run)
        scope.extensions = {'background_job_notification': notification}
        async with asyncio.timeout(60):
            return [event async for event in runtime.execute(config, scope, RuntimeInput(text))]
    await turn('plan', 'Plan compute')
    waiting = await turn('submit', 'Approve')
    assert waiting[-1].payload['workflow_status'] == 'waiting'
    assert not runtime.active
    saved = json.loads(next(tmp_path.rglob('session.json')).read_text(encoding='utf-8'))['state']
    graph = saved['execution_graph'][0] if isinstance(saved['execution_graph'], list) else saved['execution_graph']
    plan_id = graph['graph_id']
    assert graph['nodes']['step_check']['background_jobs'] == ['job-test']
    assert graph['nodes']['step_check']['status'] == 'waiting'
    provider.job['status'] = 'succeeded'
    completed = await turn('collect', 'Inspect completed job', 'job-test')
    assert completed[-1].payload['workflow_status'] == 'idle'
    saved = json.loads(next(tmp_path.rglob('session.json')).read_text(encoding='utf-8'))['state']
    graph = saved['execution_graph'][0] if isinstance(saved['execution_graph'], list) else saved['execution_graph']
    assert graph['graph_id'] == plan_id and graph['nodes']['step_check']['status'] == 'success'
    assert sum(call[1] == 'host:submit_background_job' for call in provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_MANAGED'), reason='requires Pack orchestration dependencies')
@pytest.mark.parametrize('scripted_model', ['repair'], indirect=True)
async def test_native_missing_result_gets_one_report_only_repair(tmp_path, scripted_model):
    url, calls = scripted_model
    provider = Provider(tmp_path, url)
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig('agent-a', 'Native')
    await runtime.create_agent(config)
    for run, prompt in [('plan', 'Plan a check'), ('execute', 'Approve')]:
        async with asyncio.timeout(60):
            events = [event async for event in runtime.execute(config, context(run), RuntimeInput(prompt))]
    assert provider.calls == [('agent-a', 'echo', {'value': 'once'})]
    assert any({t['function']['name'] for t in call.get('tools', [])} == {'submit_step_result'} for call in calls)
    assert any(n.role == 'step' and n.status == 'success' for n in provider.projection.nodes)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_MANAGED'), reason='requires Pack orchestration dependencies')
@pytest.mark.parametrize('scripted_model', [False, True], indirect=True, ids=['normal', 'tool-failures'])
async def test_pack_engine_planner_checkpoint_executor_recursive_child_and_oaw_tool(tmp_path, scripted_model):
    url, calls = scripted_model
    class ArtifactProvider(Provider):
        async def list_tools(self, agent):
            return await super().list_tools(agent) + [
                ScopedToolDefinition('publish', 'publish_artifact', 'Publish Sandbox output', input_schema={'type': 'object'}),
                ScopedToolDefinition('inspect', 'inspect_artifacts', 'Inspect a published version', input_schema={'type': 'object'}),
            ]

        async def invoke_tool(self, agent, capability, arguments):
            if capability == 'echo':
                return await super().invoke_tool(agent, capability, arguments)
            self.calls.append((agent, capability, arguments))
            return {'state': 'ready', 'version_id': 'v1'}

    provider = ArtifactProvider(tmp_path, url)
    config = AgentConfig('agent-a', 'Native')
    runtime = MatCreatorRuntime(provider)
    await runtime.create_agent(config)

    async def turn(run_id, prompt):
        async with asyncio.timeout(60):
            return [event async for event in runtime.execute(config, context(run=run_id), RuntimeInput(prompt))]

    planned = await turn('run1', 'Plan a bridge test')
    assert planned[-1].type.value == 'agent_completed'
    assert 'Plan ready' in planned[-1].payload['text']
    faults = any(t['function']['name'] == 'knowledge_search' for r in calls
        for m in r['messages'] if m['role'] == 'assistant' for t in m.get('tool_calls') or [])
    assert len(provider.calls) == (2 if faults else 0)
    if faults:
        # Legacy checkpoints may end with an unanswered tool call. Restoring
        # must close that exchange without claiming success or replaying it.
        path = next(tmp_path.rglob('session.json'))
        retained = json.loads(path.read_text(encoding='utf-8'))
        retained['events'].append({'id': 'orphan-event', 'invocation_id': 'old-invocation',
            'author': 'thinking_agent', 'timestamp': retained['events'][-1]['timestamp'],
            'content': {'role': 'model', 'parts': [{'function_call': {'id': 'orphan-call',
                'name': 'oaw_invoke_tool', 'args': {'capability_id': 'echo', 'arguments': {'value': 'never-replay'}}}}]}})
        path.write_text(json.dumps(retained), encoding='utf-8')
    # New runtime instance and process must load the old session/checkpoint.
    runtime = MatCreatorRuntime(provider)
    await runtime.create_agent(config)
    finished = await turn('run2', 'Approve the plan')
    assert finished[-1].type.value == 'agent_completed'
    actual = [call for call in provider.calls if not (call[1] == 'echo' and call[2]['value'].endswith(('-first', '-second')))]
    assert actual == [
        ('agent-a', 'echo', {'value': 'native-child'}),
        ('agent-a', 'publish', {'collection': 'outputs', 'path': '/workspace/result.txt'}),
        ('agent-a', 'inspect', {'collection': 'outputs', 'version_id': 'v1'}),
    ]
    assert any(e.payload.get('subagent') for e in finished)
    assert any(node.kind == 'agent' and node.parent_id for node in provider.projection.nodes)
    projected_step = next(node for node in provider.projection.nodes if node.role == 'step' and node.plan_id)
    assert projected_step.status == 'success'
    assert projected_step.execution_id and projected_step.started_at and projected_step.finished_at
    assert provider.projection.history_id
    current_publications = [value for run, value in provider.projections if run == 'run2']
    assert current_publications[-1].phase == 'idle'
    assert current_publications[-1].revision > max(
        value.revision for value in current_publications if value.phase == 'running')
    checkpoint = next(tmp_path.rglob('session.json'))
    state = json.loads(checkpoint.read_text(encoding='utf-8'))['state']
    assert state['session_id'] == 'native'
    assert state['session_workdir_initialized'] is True
    # Artifact URIs remain URIs in the persisted session log, not host paths.
    assert 'oaw:artifact:outputs:v1' in json.dumps(state)
    assert not any('oaw:artifact:' in ref and not ref.startswith('oaw:artifact:')
        for ref in state.get('session_artifacts', []))
    graph = state['execution_graph']
    if isinstance(graph, list):
        graph = graph[0]
    assert graph['nodes']['step_check']['status'] == 'success'
    assert 'test-secret' not in checkpoint.read_text(encoding='utf-8')
    assert all('test-secret' not in json.dumps(dict(event.payload)) for event in finished)
    for request in calls:
        names = {t['function']['name'] for t in request.get('tools', [])}
        assert not names & {'run_bash', 'run_python', 'submit_bohr_batchjob', 'query_knowledge_graph', 'load_skill'}
    if faults:
        responses = {m['tool_call_id']: json.loads(m['content']) for r in calls
            for m in r['messages'] if m['role'] == 'tool' and m.get('tool_call_id', '').startswith(('fault_', 'orphan-'))}
        for layer in ('planner', 'executor', 'step', 'recursive'):
            for i in range(3):
                assert responses[f'fault_{layer}_{i}']['error']['code'] == 'unknown_tool'
            for i, suffix in ((3, 'first'), (4, 'second')):
                assert responses[f'fault_{layer}_{i}']['result']['verified'] == f'{layer}-{suffix}'
                assert sum(args.get('value') == f'{layer}-{suffix}' for _, _, args in provider.calls) == 1
            assert responses[f'fault_{layer}_5']['error']['code'] == 'invalid_tool_arguments'
            assert responses[f'fault_{layer}_5']['error']['side_effects'] == 'none'
        assert responses['orphan-call']['error']['code'] == 'tool_interrupted'
        assert responses['orphan-call']['error']['side_effects'] == 'unknown'
        assert not any(args.get('value') == 'never-replay' for _, _, args in provider.calls)
        assert any(e.type.value == 'tool_completed' and e.payload.get('ok') is False for e in finished)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_MANAGED'), reason='requires Pack orchestration dependencies')
@pytest.mark.parametrize('scripted_model', [True], indirect=True)
async def test_native_tool_start_is_delivered_while_the_real_worker_is_waiting_for_its_result(tmp_path, scripted_model):
    url, _ = scripted_model
    entered, release, started = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observed = []

    class PausedProvider(Provider):
        async def invoke_tool(self, agent, capability, arguments):
            entered.set()
            await release.wait()
            return await super().invoke_tool(agent, capability, arguments)

    runtime = MatCreatorRuntime(PausedProvider(tmp_path, url))
    config = AgentConfig('agent-a', 'Native')
    await runtime.create_agent(config)

    async def consume():
        async for event in runtime.execute(config, context(), RuntimeInput('Plan a bridge test')):
            observed.append(event)
            if event.type.value == 'tool_started' and event.payload.get('name') == 'oaw_invoke_tool':
                started.set()

    task = asyncio.create_task(consume())
    try:
        async with asyncio.timeout(60):
            await entered.wait()
            # The native worker is blocked on a genuine host bridge request.
            # Start must be observable before releasing any tool result.
            await asyncio.wait_for(started.wait(), 5)
            assert not task.done()
            assert not any(event.type.value == 'tool_completed' and event.payload.get('name') == 'oaw_invoke_tool'
                and event.payload.get('ok') is True for event in observed)
            release.set()
            await task
            assert observed[-1].type.value == 'agent_completed'
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_joins_tool_and_worker_and_does_not_complete(tmp_path, monkeypatch):
    import plugins.matcreator.oaw_matcreator.native_runtime as module
    worker = tmp_path / 'native_worker.py'
    worker.write_text('''import json,sys,time
start=json.loads(sys.stdin.readline())
print(json.dumps({'type':'request','id':'pending','method':'invoke_tool','capability_id':'echo','arguments':{'value':'wait'}}),flush=True)
while sys.stdin.readline(): pass
''', encoding='utf-8')
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'native_runtime.py'))
    monkeypatch.setattr(module, 'read_environment', lambda: (sys.executable, tmp_path / 'dependencies'))
    entered, cancelled = asyncio.Event(), asyncio.Event()
    provider = Provider(tmp_path / 'state')

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    provider.invoke_tool = blocked
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig('agent-a', 'Native', provider_config={})
    await runtime.create_agent(config)
    events = []

    async def run():
        async for event in runtime.execute(config, context(), RuntimeInput('wait')):
            events.append(event)

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 5)
    process = runtime.active['run1'][1]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 8)
    assert cancelled.is_set()
    assert process.returncode is not None
    assert not runtime.active
    assert all(event.type.value != 'agent_completed' for event in events)


@pytest.mark.asyncio
@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_MANAGED'), reason='requires Pack orchestration dependencies')
async def test_managed_worker_compacts_only_over_budget_context_and_keeps_native_history(tmp_path, scripted_model):
    url, calls = scripted_model
    provider = Provider(tmp_path, url)
    provider.resolve_model = lambda _: RuntimeModelConnection('native-test', 'openai', url, 'test-secret',
        context_window=32768, max_output_tokens=1024)
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig('agent-a', 'Native')
    await runtime.create_agent(config)

    async def turn(run, prompt):
        async with asyncio.timeout(60):
            return [e async for e in runtime.execute(config, context(run=run), RuntimeInput(prompt))]

    await turn('first', 'Plan a bridge test')
    path = next(tmp_path.rglob('session.json'))
    retained = json.loads(path.read_text(encoding='utf8'))
    last_time = retained['events'][-1]['timestamp']
    for i in range(70):
        retained['events'].append({'id': f'archive-{i}', 'invocation_id': f'old-{i}',
            'author': 'user', 'timestamp': last_time + (i + 1) / 1000,
            'content': {'role': 'user', 'parts': [{'text': f'Historical observation {i}: ' + 'evidence ' * 300}]}})
    path.write_text(json.dumps(retained), encoding='utf8')
    observed = await turn('second', 'Only inspect context pressure; do not execute')
    summaries = [r for r in calls if any('Create a compact continuation checkpoint' in str(m.get('content', ''))
        for m in r['messages'] if m['role'] == 'system')]
    assert summaries and all(not r.get('tools') for r in summaries)
    assert any(e.payload.get('context_state') == 'compacting' and e.payload.get('author') == 'thinking_agent'
               for e in observed)
    restored = json.loads(path.read_text(encoding='utf8'))
    assert {e['id'] for e in restored['events']} >= {f'archive-{i}' for i in range(70)}
    assert not any(e.get('actions', {}).get('compaction') for e in restored['events'])
    assert list(tmp_path.rglob('contexts/*.json'))
    assert not runtime.active
    count = len(summaries)
    await turn('third', 'Only inspect context pressure; do not execute')
    assert len([r for r in calls if any('Create a compact continuation checkpoint' in str(m.get('content', ''))
        for m in r['messages'] if m['role'] == 'system')]) == count


@pytest.mark.asyncio
async def test_worker_crash_and_secret_redaction_and_scope_isolation(tmp_path, monkeypatch):
    import plugins.matcreator.oaw_matcreator.native_runtime as module
    worker = tmp_path / 'native_worker.py'
    worker.write_text('''import json,sys
start=json.loads(sys.stdin.readline())
print(json.dumps({'type':'error','message':'failed with '+start['model']['api_key']}),flush=True)
''', encoding='utf-8')
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'native_runtime.py'))
    monkeypatch.setattr(module, 'read_environment', lambda: (sys.executable, tmp_path / 'dependencies'))
    provider = Provider(tmp_path / 'state')
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig('agent-a', 'Native', provider_config={})
    await runtime.create_agent(config)
    for scope in ('session-a', 'session-b'):
        with pytest.raises(Exception, match=r'failed with \[redacted\]'):
            async for _ in runtime.execute(config, context(session=scope), RuntimeInput('hello')):
                pass
    assert len(list((provider.root / 'agent-a' / 'matcreator').iterdir())) == 2
    assert runtime.active == {}
