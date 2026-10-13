"""Exercise a real ADK tool/model loop with a deterministic local model."""
import asyncio
from uuid import uuid4

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

from backend.agents import GoogleAdkAgentRuntime, ScopedToolDefinition, ToolParameter
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.errors import ConflictError
from backend.conversations import ConversationPost, ConversationSessionCreate
from backend.services import create_services
from backend.tests.conversation_helpers import wait_for_conversation_delivery
from backend.world.models import CardCreate, EdgeCreate


class ToolSequenceModel(BaseLlm):
    model: str = 'openai/input-test'
    _requests: list = PrivateAttr(default_factory=list)

    async def generate_content_async(self, request, stream=False):
        wire = '\n'.join(content.model_dump_json() for content in request.contents)
        self._requests.append(wire)
        if 'Skip c and summarize b' in wire:
            assert 'result-of-b' in wire
            part = types.Part.from_text(text='I saw your update after b; c was skipped.')
        elif len(self._requests) <= 3:
            part = types.Part.from_function_call(name='step', args={'label': 'abc'[len(self._requests) - 1]})
        else:
            part = types.Part.from_text(text='Finished a, b, c.')
        yield LlmResponse(content=types.Content(role='model', parts=[part]))


class BlockingToolProvider(WorldAgentCapabilityProvider):
    def __init__(self, services):
        super().__init__(services)
        self.in_b = asyncio.Event()
        self.release_b = asyncio.Event()
        self.calls = []

    async def list_tools(self, agent_id):
        return [ScopedToolDefinition('fixture:step', 'step', 'Run one test step', (ToolParameter('label'),))]

    async def invoke_tool(self, agent_id, capability_id, arguments):
        label = arguments['label']
        self.calls.append(label)
        if label == 'b':
            self.in_b.set()
            await self.release_b.wait()
        return {'result': f'result-of-{label}'}


@pytest.mark.asyncio
@pytest.mark.parametrize('managed', [False, True])
async def test_message_during_b_reaches_next_model_call_without_another_run(data_root, monkeypatch, managed):
    services = create_services(Settings.for_data_root(data_root))
    provider = BlockingToolProvider(services)
    model = ToolSequenceModel()
    runtime = GoogleAdkAgentRuntime(provider, context_store=services.contexts if managed else None)
    monkeypatch.setattr(runtime, '_adk_model', lambda _: model)
    services.install_runtime_provider('google.adk', runtime, default=True)
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Worker',
            config={'model': 'openai/input-test', 'max_concurrent_runs': 2}))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        await services.post_conversation_message(room.id, session.id,
            ConversationPost(content='Execute a, b, c in sequence.', mention_agent_ids=[agent.id]))
        await asyncio.wait_for(provider.in_b.wait(), 30)
        request = ConversationPost(message_id=uuid4(), content='Skip c and summarize b', mention_agent_ids=[agent.id])
        sent = await services.post_conversation_message(room.id, session.id, request)
        with pytest.raises(ConflictError):
            await services.post_conversation_message(room.id, session.id, request)
        await asyncio.sleep(.05)
        assert len(services.run_manager.list_runs(agent_id=agent.id)) == 1
        assert len(model._requests) == 2  # b has not returned yet.
        provider.release_b.set()
        await wait_for_conversation_delivery(services, room.id, session.id)
        runs = services.run_manager.list_runs(agent_id=agent.id)
        assert len(runs) == 1 and runs[0].status == 'succeeded', runs
        assert provider.calls == ['a', 'b']
        assert len(model._requests) == 3
        wire = model._requests[-1]
        assert wire.index('result-of-b') < wire.index('Skip c and summarize b')
        assert wire.count('Skip c and summarize b') == 1
        assert sent.message.id in runs[0].lifecycle['delivery_message_ids']
        page = services.conversations.page_messages(room.id, session.id)
        assert page.deliveries == []
        assert [m.content for m in page.items] == [
            'Execute a, b, c in sequence.', 'Skip c and summarize b', 'I saw your update after b; c was skipped.']
    finally:
        provider.release_b.set()
        await services.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_live_input_is_scoped_reauthorized_and_acknowledged_once(data_root):
    from types import SimpleNamespace
    from backend.agents import MockAgentRuntime
    from backend.errors import PermissionDeniedError, ResourceValidationError
    from backend.runs.manager import _current_invocation
    from backend.runs.models import RunStatus

    services = create_services(Settings.for_data_root(data_root))
    provider = WorldAgentCapabilityProvider(services)
    services.install_runtime_provider('core.mock', MockAgentRuntime(provider), default=True)
    token = None
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Worker'))
        peer = await services.create_card(CardCreate(type='agent', name='Peer'))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        edge = await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        await services.create_edge(EdgeCreate(source=peer.id, target=room.id, relationship='participate'))
        first = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id, peer.id]))
        second = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        def add(session, content, target):
            return services.conversations.add_message(room.id, session.id, sender_kind='user',
                sender_id=None, sender_name='You', content=content, mention_agent_ids=[target], delivery_agent_ids=[target])
        # An older queued message in another session must not block this Run's inbox.
        other = add(second, 'Other session', agent.id)
        mine = add(first, 'Current session', agent.id)
        peers = add(first, 'Only for Peer', peer.id)
        manager = services.run_manager
        run = manager.store.create(agent_id=agent.id, runtime_provider_id='core.mock',
            caller_kind='conversation', caller_id=room.id, context_id=first.id)
        manager.store.update_status(run.run_id, RunStatus.RUNNING)
        token = _current_invocation.set(SimpleNamespace(agent_id=agent.id, run_id=run.run_id))
        assert [m['id'] for m in await provider.pending_conversation_input(agent.id, run.run_id)] == [mine.id]
        with pytest.raises(ResourceValidationError):
            await provider.pending_conversation_input(peer.id, run.run_id)
        await provider.acknowledge_conversation_input(agent.id, run.run_id, [mine.id, other.id, peers.id])
        await provider.acknowledge_conversation_input(agent.id, run.run_id, [mine.id])
        assert await provider.pending_conversation_input(agent.id, run.run_id) == []
        assert manager.get_run(run.run_id).lifecycle['delivery_message_ids'] == [mine.id]
        assert services.deliveries.pending_messages(agent.id, room.id, second.id) == [other.id]
        assert services.deliveries.pending_messages(peer.id, room.id, first.id) == [peers.id]
        revoked = add(first, 'Revoked input', agent.id)
        services.world.delete_edge(edge.id)
        with pytest.raises(PermissionDeniedError):
            await provider.pending_conversation_input(agent.id, run.run_id)
        with pytest.raises(PermissionDeniedError):
            await provider.acknowledge_conversation_input(agent.id, run.run_id, [revoked.id])
        assert services.deliveries.pending_messages(agent.id, room.id, first.id) == [revoked.id]
    finally:
        if token is not None:
            _current_invocation.reset(token)
        services.close()
