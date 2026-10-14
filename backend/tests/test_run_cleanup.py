import asyncio
import pytest
from backend.tests.test_runs import RecordingProvider, _services, HangingStopProvider
from backend.tests.test_runs import TerminalAcloseProvider
from backend.tests.conversation_helpers import wait_for_conversation_run, wait_for_conversation_delivery
from backend.world.models import CardCreate, EdgeCreate
from backend.conversations import ConversationPost, ConversationSessionCreate
from backend.runs import RunStatus
from backend.errors import RuntimeUnavailableError


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', [1, 2])
async def test_followups_wait_for_terminal_stream_cleanup(tmp_path, limit):
    provider = TerminalAcloseProvider()
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        agent = await services.create_card(CardCreate(type='agent', config={'max_concurrent_runs': limit}))
        room = await services.create_card(CardCreate(type='conversation'))
        await services.create_edge(EdgeCreate(source=agent.id, target=room.id, relationship='participate'))
        session = await services.create_conversation_session(room.id, ConversationSessionCreate(participant_ids=[agent.id]))
        sent = await services.post_conversation_message(room.id, session.id,
            ConversationPost(content='first', mention_agent_ids=[agent.id]))
        first = await wait_for_conversation_run(services, sent.message.id, agent.id)
        followup = await services.post_conversation_message(room.id, session.id,
            ConversationPost(content='next', mention_agent_ids=[agent.id]))
        provider.emit_terminal.set()
        await asyncio.wait_for(provider.aclose_started.wait(), 2)
        assert manager.get_run(first.run_id).status == RunStatus.SUCCEEDED
        assert manager.holds_agent_slot(first.run_id)
        if limit == 1:
            with pytest.raises(RuntimeUnavailableError):
                await manager.start_run(agent.id, 'direct call must also wait')
        # Exercise admission during cleanup, including with a spare Agent slot.
        await services._drain_conversation_agent(agent.id)
        assert len(manager.list_runs(agent_id=agent.id)) == 1
        assert services.deliveries.pending_messages(agent.id, room.id, session.id) == [followup.message.id]
        provider.release_aclose.set()
        await wait_for_conversation_delivery(services, room.id, session.id)
        runs = manager.list_runs(agent_id=agent.id)
        assert len(runs) == 2 and all(r.status == RunStatus.SUCCEEDED for r in runs)
        assert not manager.holds_agent_slot(first.run_id)
    finally:
        provider.emit_terminal.set()
        provider.release_aclose.set()
        await services.shutdown()
        services.close()
from backend.agents import AgentEvent, AgentEventType
import contextvars


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [None, 'agent', 'run'])
async def test_provider_stream_keeps_one_context_through_stop_and_next_turn(tmp_path, cancel):
    span = contextvars.ContextVar('provider_span', default=None)

    class ContextProvider(RecordingProvider):
        async def execute(self, config, context, runtime_input):
            token = span.set(context.run_id)
            owner = asyncio.current_task()
            try:
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.TOOL_STARTED, {'name': 'work'})
                assert span.get() == context.run_id
                assert asyncio.current_task() is owner
                self.started.set()
                if self.mode == 'block':
                    await self.continue_tool.wait()
                yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED, {}, run_status=RunStatus.SUCCEEDED)
            finally:
                span.reset(token)

    provider = ContextProvider(mode='block' if cancel else 'success')
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        agent = await services.create_card(CardCreate(type='agent'))
        conversation = await services.create_card(CardCreate(type='conversation'))
        await services.create_edge(EdgeCreate(source=agent.id, target=conversation.id, relationship='participate'))
        session = await services.create_conversation_session(conversation.id,
            ConversationSessionCreate(title='Stop and resend', participant_ids=[agent.id]))
        sent = await services.post_conversation_message(conversation.id, session.id,
            ConversationPost(content='first turn', mention_agent_ids=[agent.id]))
        first = await wait_for_conversation_run(services, sent.message.id, agent.id)
        if cancel:
            await asyncio.wait_for(provider.started.wait(), 1)
            if cancel == 'agent':
                await services.stop_agent(agent.id)
            else:
                await manager.cancel_run(first.run_id)
        await asyncio.wait_for(manager.wait_execution(first.run_id), 1)
        record = manager.get_run(first.run_id)
        assert record.status == (RunStatus.CANCELLED if cancel else RunStatus.SUCCEEDED)
        assert record.lifecycle.get('cleanup') not in {'pending', 'failed'}
        await wait_for_conversation_delivery(services, conversation.id, session.id)
        provider.mode = 'success'
        sent = await services.post_conversation_message(conversation.id, session.id,
            ConversationPost(content='npm install -g @dptech-corp/bohr-cli@latest', mention_agent_ids=[agent.id]))
        assert sent.accepted_agent_ids == [agent.id]
        second = await wait_for_conversation_run(services, sent.message.id, agent.id)
        assert second.run_id != first.run_id
        await asyncio.wait_for(manager.wait_execution(second.run_id), 1)
        assert manager.get_run(second.run_id).status == RunStatus.SUCCEEDED
        await wait_for_conversation_delivery(services, conversation.id, session.id)
    finally:
        provider.continue_tool.set()
        await services.shutdown()


@pytest.mark.asyncio
async def test_quiet_registered_tool_outlives_chat_inactivity(tmp_path):
    provider = RecordingProvider(mode='tool')
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        manager.inactivity_timeout_seconds = .02
        agent = await services.create_card(CardCreate(type='agent'))
        run = await manager.start_run(agent.id, 'quiet tool')
        await provider.tool_started.wait()
        await asyncio.sleep(.08)
        assert manager.get_run(run.run_id).status == RunStatus.RUNNING
        assert manager.get_run(run.run_id).lifecycle['active_tools'] == 1
        provider.continue_tool.set()
        await manager.wait_execution(run.run_id)
        assert manager.get_run(run.run_id).status == RunStatus.SUCCEEDED
    finally:
        services.close()


@pytest.mark.asyncio
async def test_cleanup_pending_is_durable_and_retry_joins_same_stop(tmp_path):
    provider = HangingStopProvider()
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        manager.cleanup_timeout_seconds = .02
        agent = await services.create_card(CardCreate(type='agent'))
        run = await manager.start_run(agent.id, 'blocked')
        await provider.started.wait()
        with pytest.raises(RuntimeUnavailableError, match='pending'):
            await manager.cancel_run(run.run_id)
        record = manager.get_run(run.run_id)
        assert record.status == RunStatus.CANCELLED
        assert record.lifecycle['cleanup'] == 'pending'
        assert manager.holds_agent_slot(run.run_id)
        provider.release_stop.set()
        await manager._cleanup_tasks[run.run_id]
        assert manager.get_run(run.run_id).lifecycle['cleanup_reason'] is None
        manager.assert_can_start(agent.id)
        record = await manager.cancel_run(run.run_id)
        assert record.lifecycle['cleanup'] == 'complete'
        assert provider.stopped_run_ids == [run.run_id]
    finally:
        services.close()


@pytest.mark.asyncio
async def test_terminal_stream_cleanup_timeout_blocks_admission_until_stop(tmp_path):
    provider = TerminalAcloseProvider()
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        manager.cleanup_timeout_seconds = .03
        agent = await services.create_card(CardCreate(type='agent'))
        run = await manager.start_run(agent.id, 'finish then stall cleanup')
        provider.emit_terminal.set()
        await asyncio.wait_for(manager.wait_execution(run.run_id), 2)
        record = manager.get_run(run.run_id)
        assert record.status == RunStatus.SUCCEEDED  # Preserve the result.
        assert record.lifecycle['cleanup'] == 'failed'
        assert 'TimeoutError' in record.lifecycle['cleanup_reason']
        assert manager.holds_agent_slot(run.run_id)
        with pytest.raises(RuntimeUnavailableError, match='cleanup'):
            manager.assert_can_start(agent.id)
        await manager.cancel_run(run.run_id)
        assert not manager.holds_agent_slot(run.run_id)
        manager.assert_can_start(agent.id)
    finally:
        provider.release_aclose.set()
        await services.shutdown()
        services.close()


@pytest.mark.asyncio
async def test_dependent_cancellation_preserves_detached_attempt(tmp_path):
    provider = RecordingProvider(mode='block')
    services = _services(tmp_path, provider)
    try:
        agent = await services.create_card(CardCreate(type='agent', config={'max_concurrent_runs': 3}))
        manager = services.run_manager
        parent = await manager.start_run(agent.id, 'parent')
        child = await manager.start_run(agent.id, 'child', parent_run_id=parent.run_id)
        detached = await manager.start_run(agent.id, 'independent', detached=True)
        await manager.cancel_run(parent.run_id)
        assert manager.get_run(child.run_id).status == RunStatus.CANCELLED
        assert manager.get_run(detached.run_id).status == RunStatus.RUNNING
        assert manager.get_run(detached.run_id).lifecycle['owner_id'] == agent.id
        await manager.cancel_run(detached.run_id)
    finally:
        services.close()


@pytest.mark.asyncio
async def test_quiet_tool_still_has_execution_deadline(tmp_path):
    provider = RecordingProvider(mode='tool')
    services = _services(tmp_path, provider)
    try:
        manager = services.run_manager
        manager.execution_deadline_seconds = .05
        manager.inactivity_timeout_seconds = .01
        agent = await services.create_card(CardCreate(type='agent'))
        record = await manager.start_run(agent.id, 'tool exceeding deadline')
        await asyncio.wait_for(manager.wait_execution(record.run_id), 1)
        assert manager.get_run(record.run_id).status == RunStatus.FAILED
        assert 'deadline' in manager.get_run(record.run_id).error
    finally:
        services.close()

