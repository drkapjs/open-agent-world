"""Executed by test_tool_failures in the private dependency environment."""
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, sys.argv[1])
from native_environment import activate, activate_engine
activate(Path(sys.argv[2]))
activate_engine()
root = Path(sys.argv[3])
os.environ.update(MATCREATOR_HOME=str(root / 'home'), MATCLAW_WORKSPACE=str(root / 'workspace'),
    LLM_MODEL='openai/test', LLM_API_KEY='test-secret', LLM_BASE_URL='http://127.0.0.1:1')

from google.adk.events import Event
from google.genai import types
from oaw_matcreator_engine.tool_execution import ResilientFunctionTool
from oaw_matcreator_engine.session_recovery import repair_interrupted_tools
from oaw_matcreator_engine.resources import configure, bridge


async def failures():
    for kind, code in [('bug', 'tool_execution_error'), ('timeout', 'tool_timeout'),
                       ('cancel', 'tool_cancelled'), ('self_cancel', 'tool_cancelled'),
                       ('bad_result', 'tool_execution_error'), ('nan', 'tool_execution_error')]:
        calls = []

        async def operation() -> dict:
            calls.append('effect')
            if kind == 'bug':
                raise RuntimeError('private-test-secret')
            if kind == 'timeout':
                raise TimeoutError('private-test-secret')
            if kind == 'self_cancel':
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            if kind == 'cancel':
                raise asyncio.CancelledError()
            return {'value': object() if kind == 'bad_result' else float('nan')}

        result = await ResilientFunctionTool(operation).run_async(args={}, tool_context=None)
        assert result['error']['code'] == code, (kind, result)
        assert result['error']['side_effects'] == 'unknown'
        assert 'private-test-secret' not in json.dumps(result)
        assert calls == ['effect']
        assert asyncio.current_task().cancelling() == 0

    part = types.Part.from_bytes(data=b'\x89PNG\xff', mime_type='image/png')
    payload = {'ok': True, 'media': [part]}

    async def image() -> dict:
        return payload

    assert await ResilientFunctionTool(image).run_async(args={}, tool_context=None) is payload

    calls = []

    async def typed(value: str, number: int = 1) -> dict:
        calls.append(value)
        return {'value': value, 'number': number}

    tool = ResilientFunctionTool(typed)
    for args in ({}, {'value': ['private-test-secret']}, {'value': 'ok', 'extra': True}, {'value': 'ok', 'number': 'two'}):
        result = await tool.run_async(args=args, tool_context=None)
        assert result['error']['code'] == 'invalid_tool_arguments'
        assert result['error']['side_effects'] == 'none'
        assert 'private-test-secret' not in json.dumps(result)
    assert calls == []
    assert await tool.run_async(args={'value': 'corrected'}, tool_context=None) == {'value': 'corrected', 'number': 1}
    assert calls == ['corrected']


async def cancellation():
    for cleanup in ('normal', 'suppress', 'error'):
        entered, cleaned = asyncio.Event(), asyncio.Event()

        async def operation() -> dict:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if cleanup == 'suppress':
                    return {'ok': True}
                if cleanup == 'error':
                    raise RuntimeError('private cleanup error')
                raise
            finally:
                cleaned.set()

        task = asyncio.create_task(ResilientFunctionTool(operation).run_async(args={}, tool_context=None))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        try:
            await task
            raise AssertionError('Run Stop was swallowed')
        except asyncio.CancelledError:
            pass
        assert cleaned.is_set() and task.cancelled()


def history():
    def event(invocation, calls=(), responses=()):
        return Event(author='thinking_agent', invocation_id=invocation, content=types.Content(role='model', parts=[
            *[types.Part(function_call=types.FunctionCall(id=i, name='same_tool', args={})) for i in calls],
            *[types.Part(function_response=types.FunctionResponse(id=i, name='same_tool', response={'ok': True})) for i in responses],
        ]))

    events = [event('old', calls=['a', 'b']), event('old', responses=['a']), event('new', calls=['a'])]
    repaired = repair_interrupted_tools(events)
    assert len(repaired) == 5
    assert repaired[0] is events[0] and repaired[2] is events[1] and repaired[3] is events[2]
    missing = [e.content.parts[0].function_response for e in (repaired[1], repaired[4])]
    assert [r.id for r in missing] == ['b', 'a']
    assert all(r.response['error']['side_effects'] == 'unknown' for r in missing)
    assert repair_interrupted_tools(repaired) == repaired
    another_agent = event('old', calls=['a'])
    another_agent.author = 'execution_orchestrator'
    assert len(repair_interrupted_tools([*repaired, another_agent])) == len(repaired) + 2


async def resources():
    async def rpc(method, **kwargs):
        return {'ok': False, 'error': {'code': 'permission_denied', 'message': 'Access revoked'}}

    configure(rpc, lambda *args, **kwargs: None)
    assert (await bridge().oaw_list_tools())['error']['code'] == 'permission_denied'
    assert (await bridge().oaw_invoke_tool('x', {}))['error']['code'] == 'permission_denied'

    async def encoded_error(method, **kwargs):
        return {'ok': False, 'result': [{'type': 'inputText', 'text': json.dumps({
            'ok': False, 'error': {'code': 'invalid_resource', 'message': 'Correct input'}})}]}

    configure(encoded_error, lambda *args, **kwargs: None)
    assert (await bridge().oaw_invoke_tool('x', {}))['error']['code'] == 'invalid_resource'


async def same_name_history():
    from oaw_matcreator_engine.agents.execution_agent.step_executor_runner import _stream_step_events
    records = []

    class Graph:
        def log_conversation_event(self, *args):
            pass

        def log_tool_call(self, step, record):
            records.append(record)

    class Runner:
        async def run_async(self, **kwargs):
            yield Event(author='step_executor', content=types.Content(role='model', parts=[
                types.Part(function_call=types.FunctionCall(id=i, name='oaw_invoke_tool', args={'value': i})) for i in ('a', 'b')]))
            yield Event(author='step_executor', content=types.Content(role='user', parts=[
                types.Part(function_response=types.FunctionResponse(id=i, name='oaw_invoke_tool', response={'value': i})) for i in ('b', 'a')]))

    await _stream_step_events(Runner(), SimpleNamespace(user_id='u', id='s'), None,
        'step', SimpleNamespace(state={}), Graph())
    assert len(records) == 2
    assert all(r['input']['value'] == r['output']['value'] == r['call_id'] for r in records)
    from oaw_matcreator_engine.agents.thinking_agent.planning import set_node_status
    context = SimpleNamespace(state={'execution_graph': {'nodes': {'node': {'status': 'pending'}}}})
    assert set_node_status('node', 'invented-state', tool_context=context)['status'] == 'error'
    assert context.state['execution_graph']['nodes']['node']['status'] == 'pending'


async def main():
    await failures()
    await cancellation()
    history()
    await resources()
    await same_name_history()
    print('tool failure contracts passed')


asyncio.run(main())
