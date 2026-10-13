"""Real HTTP visual fixtures with an offline MatCreator Sandbox.

No scientific process is started by this suite. Keep the production preset's
network intent intact and make the isolated browser profile portable to hosts
without the optional Windows network-policy broker.
"""
from backend.config import Settings
from backend.main import create_app
from backend.services import create_services

settings = Settings.from_environment()
services = create_services(settings)
for preset_id in ("matcreator.native-research",):
    preset = services.plugins._legion_presets[preset_id]
    services.plugins._legion_presets[preset.id] = preset.model_copy(update={
        "nodes": tuple(node.model_copy(update={"config": {**node.config, "network_enabled": False}})
                       if node.type == "sandbox" else node for node in preset.nodes),
    })
app = create_app(settings, services=services)


# Conversation integration fixture: real persistence/delivery and HTTP/WS,
# deterministic provider output, no external model or Sandbox process.
import asyncio
from backend.agents import AgentEvent, AgentEventType, MockAgentRuntime

_conversation_gates = {}
_conversation_original_runtime = None


@app.post('/api/test/conversation-input/install')
async def install_conversation_input_fixture():
    global _conversation_original_runtime
    _conversation_original_runtime = services.run_manager.provider_for_agent_configuration('core.mock')
    provider = WorldAgentCapabilityProvider(services)

    class PausedConversation(MockAgentRuntime):
        async def execute(self, config, context, runtime_input):
            gate = _conversation_gates[context.run_id] = asyncio.Event()
            try:
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                    {'text': 'I finished a. I am checking b now.', 'provider_message_id': 'before-b', 'final': True})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_STARTED,
                    {'name': 'b', 'call_id': 'tool-b', 'arguments': {'query': 'Check the intermediate result'}})
                await gate.wait()
                pending = await provider.pending_conversation_input(context.agent_id, context.run_id)
                assert any('Skip c' in message['text'] for message in pending)
                await provider.acknowledge_conversation_input(context.agent_id, context.run_id, [m['id'] for m in pending])
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_COMPLETED,
                    {'name': 'b', 'call_id': 'tool-b', 'response': {'result': 'b returned'}})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                    {'text': 'I received your update with the result of b. I skipped c.', 'provider_message_id': 'after-b', 'final': True})
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status='succeeded')
            finally:
                _conversation_gates.pop(context.run_id, None)

    services.install_runtime_provider('core.mock', PausedConversation(provider), default=True)
    return {'ok': True}


@app.post('/api/test/conversation-input/restore')
async def restore_conversation_input_fixture():
    global _conversation_original_runtime
    if _conversation_original_runtime is not None:
        services.install_runtime_provider('core.mock', _conversation_original_runtime, default=True)
        _conversation_original_runtime = None
    return {'ok': True}


@app.post('/api/test/conversation-input/{run_id}/return-b')
async def return_conversation_tool(run_id: str):
    _conversation_gates[run_id].set()
    return {'ok': True}


# Isolated browser fixtures only: seed a provider projection through its real
# public API, without a model request or mutable document on the graph viewer.
from open_agent_world.plugin_api import ExecutionSnapshot
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.runs.manager import _current_invocation
from types import SimpleNamespace


@app.post('/api/test/execution/{agent_id}/{session_id}')
async def execution_fixture(agent_id: str, session_id: str, value: ExecutionSnapshot):
    run = (services.run_manager.get_run(value.run_id) if value.run_id else
        services.run_manager.store.create(agent_id=agent_id, runtime_provider_id='matcreator.native',
            caller_kind='conversation', context_id=session_id))
    assert run.agent_id == agent_id and run.context_id == session_id
    token = _current_invocation.set(SimpleNamespace(agent_id=agent_id, run_id=run.run_id))
    try:
        await WorldAgentCapabilityProvider(services).publish_execution(agent_id, run.run_id, value.model_dump())
    finally:
        _current_invocation.reset(token)
    return {'run_id': run.run_id}


@app.post('/api/test/execution/{agent_id}/{session_id}/start')
async def execution_start_fixture(agent_id: str, session_id: str):
    from backend.runs.models import RunStatus
    run = services.run_manager.store.create(agent_id=agent_id, runtime_provider_id='matcreator.native',
        caller_kind='conversation', context_id=session_id)
    await services.run_manager.transition_run(run.run_id, RunStatus.RUNNING)
    return {'run_id': run.run_id}
