"""Process/protocol tests plus opt-in real upstream + real ADK integration.

OAW_MATCREATOR_MANAGED=1 uses the default runtime prepared by normal OAW setup.
OAW_MATCREATOR_SOURCE optionally overrides it with a pinned devel checkout.
OAW_MATCREATOR_PYTHON selects a separately installed development environment.
No external model, credentials, remote service or scientific compute is used.
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


@pytest.mark.asyncio
async def test_bad_source_fails_as_run_error_without_host_import(tmp_path):
    provider = Provider(tmp_path)
    runtime = MatCreatorRuntime(provider)
    source = tmp_path / 'changed'
    (source / 'src/matcreator').mkdir(parents=True)
    (source / 'src/matcreator/__init__.py').write_text('raise AssertionError("must not import")')
    config = AgentConfig('a', 'Native', provider_config={'source_directory': str(source), 'python_executable': sys.executable})
    await runtime.create_agent(config)
    with pytest.raises(Exception, match='Unsupported MatCreator source'):
        async for _ in runtime.execute(config, context(agent='a'), RuntimeInput('hello')):
            pass
    assert runtime.active == {}


@pytest.fixture
def scripted_model():
    """A deterministic OpenAI endpoint; the actual upstream agents choose tools."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    calls = []
    planner_round = 0
    executor_round = 0

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
            if 'run_flash_step' in names:
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
                results = [m for m in messages if m['role'] == 'tool']
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
                if delta.get('tool_calls'):
                    delta['tool_calls'][0]['index'] = 0
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
@pytest.mark.skipif(not any(os.environ.get(key) for key in ('OAW_MATCREATOR_SOURCE', 'OAW_MATCREATOR_PYTHON', 'OAW_MATCREATOR_MANAGED')), reason='requires pinned MatCreator runtime')
async def test_real_upstream_planner_checkpoint_executor_recursive_child_and_oaw_tool(tmp_path, scripted_model):
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
    config = AgentConfig('agent-a', 'Native', provider_config={
        'source_directory': os.environ.get('OAW_MATCREATOR_SOURCE', ''),
        'python_executable': os.environ.get('OAW_MATCREATOR_PYTHON', ''),
    })
    runtime = MatCreatorRuntime(provider)
    await runtime.create_agent(config)

    async def turn(run_id, prompt):
        async with asyncio.timeout(60):
            return [event async for event in runtime.execute(config, context(run=run_id), RuntimeInput(prompt))]

    planned = await turn('run1', 'Plan a bridge test')
    assert planned[-1].type.value == 'agent_completed'
    assert 'Plan ready' in planned[-1].payload['text']
    assert provider.calls == []
    # New runtime instance and process must load the old session/checkpoint.
    runtime = MatCreatorRuntime(provider)
    await runtime.create_agent(config)
    finished = await turn('run2', 'Approve the plan')
    assert finished[-1].type.value == 'agent_completed'
    assert provider.calls == [
        ('agent-a', 'echo', {'value': 'native-child'}),
        ('agent-a', 'publish', {'collection': 'outputs', 'path': '/workspace/result.txt'}),
        ('agent-a', 'inspect', {'collection': 'outputs', 'version_id': 'v1'}),
    ]
    assert any(e.payload.get('subagent') for e in finished)
    assert any(node.kind == 'agent' and node.parent_id for node in provider.projection.nodes)
    projected_step = next(node for node in provider.projection.nodes if node.id == 'plan:step_check')
    assert projected_step.status == 'success'
    checkpoint = next(tmp_path.rglob('session.json'))
    state = json.loads(checkpoint.read_text(encoding='utf-8'))['state']
    assert state['session_id'] == 'native'
    assert state['session_workdir_initialized'] is True
    graph = state['execution_graph']
    if isinstance(graph, list):
        graph = graph[0]
    assert graph['nodes']['step_check']['status'] == 'success'
    assert 'test-secret' not in checkpoint.read_text(encoding='utf-8')
    assert all('test-secret' not in json.dumps(dict(event.payload)) for event in finished)
    for request in calls:
        names = {t['function']['name'] for t in request.get('tools', [])}
        assert not names & {'run_bash', 'run_python', 'submit_bohr_batchjob', 'query_knowledge_graph', 'load_skill'}


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
    config = AgentConfig('agent-a', 'Native', provider_config={'python_executable': sys.executable})
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
async def test_worker_crash_and_secret_redaction_and_scope_isolation(tmp_path, monkeypatch):
    import plugins.matcreator.oaw_matcreator.native_runtime as module
    worker = tmp_path / 'native_worker.py'
    worker.write_text('''import json,sys
start=json.loads(sys.stdin.readline())
print(json.dumps({'type':'error','message':'failed with '+start['model']['api_key']}),flush=True)
''', encoding='utf-8')
    monkeypatch.setattr(module, '__file__', str(tmp_path / 'native_runtime.py'))
    provider = Provider(tmp_path / 'state')
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig('agent-a', 'Native', provider_config={'python_executable': sys.executable})
    await runtime.create_agent(config)
    for scope in ('session-a', 'session-b'):
        with pytest.raises(Exception, match=r'failed with \[redacted\]'):
            async for _ in runtime.execute(config, context(session=scope), RuntimeInput('hello')):
                pass
    assert len(list((provider.root / 'agent-a' / 'matcreator').iterdir())) == 2
    assert runtime.active == {}
