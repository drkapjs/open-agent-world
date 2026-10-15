import pytest
import asyncio

from backend.agents import MockAgentRuntime, AgentEvent, AgentEventType
from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.config import Settings
from backend.conversations import ConversationSessionCreate, ConversationPost
from backend.services import create_services
from backend.tests.conversation_helpers import wait_for_conversation_delivery
from backend.world.models import CardCreate, EdgeCreate


class StreamingRuntime(MockAgentRuntime):
    async def execute(self, config, context, runtime_input):
        for key, text in [('first', 'Checking'), ('first', 'Checking resources'),
                          ('first', 'Checking resources.'), ('first', 'Checking resources.'),
                          ('second', 'Checking resources.')]:
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                             {'text': text, 'provider_message_id': key})
        yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status='succeeded')


@pytest.mark.asyncio
async def test_stream_snapshots_preserve_distinct_messages_and_stable_sequences(data_root):
    settings = Settings.for_data_root(data_root)
    services = create_services(settings)
    services.install_runtime_provider('core.mock', StreamingRuntime(WorldAgentCapabilityProvider(services)), default=True)
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Writer'))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        for turn in range(2):
            await services.post_conversation_message(room.id, session.id, ConversationPost(content=f'Start {turn}', mention_agent_ids=[agent.id]))
            await wait_for_conversation_delivery(services, room.id, session.id)
            timeline = services.conversations.page_messages(room.id, session.id).items
            # Cumulative snapshots update one row; distinct provider messages
            # remain distinct even when they happen to contain identical text.
            assert len(timeline) == (turn + 1) * 3
            assert timeline[-1].sender_kind == 'agent'
            assert timeline[-1].content == 'Checking resources.'
            assert timeline[-1].is_final
        assert len({m.id for m in timeline}) == 6
        assert timeline[1].run_id != timeline[4].run_id
        assert [m.sequence for m in timeline] == list(range(1, 7))
        ids = [m.id for m in timeline]
    finally:
        await services.shutdown()
        services.close()
    restored = create_services(settings)
    try:
        assert [m.id for m in restored.conversations.page_messages(room.id, session.id).items] == ids
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_followup_keeps_its_position_between_streamed_agent_messages(data_root):
    started, release = asyncio.Event(), asyncio.Event()

    class Paused(StreamingRuntime):
        async def execute(self, config, context, runtime_input):
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                {'text': 'Before b', 'provider_message_id': 'before'})
            # A tool boundary lets the host persist the preceding text first.
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_STARTED, {'name': 'b'})
            started.set()
            await release.wait()
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                {'text': 'Before b (finished streaming)', 'provider_message_id': 'before'})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.MESSAGE,
                {'text': 'After b', 'provider_message_id': 'after', 'final': True})
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, run_status='succeeded')

    services = create_services(Settings.for_data_root(data_root))
    services.install_runtime_provider('core.mock', Paused(WorldAgentCapabilityProvider(services)), default=True)
    try:
        agent = await services.create_card(CardCreate(type='agent', name='Writer'))
        room = await services.create_card(CardCreate(type='conversation', name='Room'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        await services.post_conversation_message(room.id, session.id, ConversationPost(content='Start', mention_agent_ids=[agent.id]))
        await asyncio.wait_for(started.wait(), 5)
        before = services.conversations.page_messages(room.id, session.id).items[-1]
        await services.post_conversation_message(room.id, session.id, ConversationPost(content='During b'))
        assert [m.content for m in services.conversations.page_messages(room.id, session.id).items] == ['Start', 'Before b', 'During b']
        release.set()
        await wait_for_conversation_delivery(services, room.id, session.id)
        after = services.conversations.page_messages(room.id, session.id).items
        assert [m.content for m in after] == ['Start', 'Before b (finished streaming)', 'During b', 'After b']
        assert (after[1].id, after[1].sequence, after[1].created_at) == (before.id, before.sequence, before.created_at)
        assert all(m.is_final for m in after)
    finally:
        release.set()
        await services.shutdown()
        services.close()
