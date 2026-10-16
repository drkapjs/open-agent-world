"""Metadata survives bounded UI history and distinguishes physical delivery."""
import importlib.util
import json
from pathlib import Path

import pytest

from backend.agents import AgentEvent, AgentEventType
from backend.runs import RunStatus
from backend.runs.activity import activity_update
from backend.events.models import EventType
from backend.tests.test_runs import RecordingProvider, _services
from backend.world.models import CardCreate


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
async def test_final_snapshot_is_published_without_a_duplicate_terminal_event(tmp_path, status):
    class Provider(RecordingProvider):
        async def execute(self, config, context, runtime_input):
            if status == "failed":
                raise RuntimeError("fixture failure")
            yield AgentEvent(context.agent_id, context.run_id, AgentEventType.COMPLETED,
                             {"text": "fixture"}, run_status=status)
    services = _services(tmp_path, Provider())
    try:
        agent = await services.create_card(CardCreate(type="agent", name="Fixture"))
        async with services.events.subscribe() as queue:
            manager = services._require_run_manager()
            run = await manager.start_run(agent.id, "fixture")
            settled = await manager.wait_execution(run.run_id)
            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
        snapshots = [e for e in events if e.type is EventType.AGENT_STATUS_CHANGED and "run" in e.payload]
        assert len(snapshots) == 1
        assert snapshots[0].payload["run"]["lifecycle"]["execution"] == "finished"
        assert snapshots[0].payload["run"]["status"] == settled.status.value == status
        assert len([e for e in events if e.type.value == "run_" + status]) == 1
    finally:
        services.close()


def test_metadata_is_bounded_without_saving_source_or_reasoning():
    lifecycle = {}
    for i in range(1030):
        lifecycle.update(activity_update(lifecycle, "tool_started", {"name": "write", "call_id": str(i),
            "arguments": {"content": "private source"}}, str(i), str(i)))
        lifecycle.update(activity_update(lifecycle, "tool_completed", {"name": "write", "call_id": str(i),
            "response": {"stdout": "private output", "exit_code": 0}}, str(i), str(i)))
    assert lifecycle["tool_metrics"]["started"] == lifecycle["tool_metrics"]["completed"] == 1030
    assert len(lifecycle["tool_calls"]) == 1024 and lifecycle["tool_calls_truncated"]
    assert "private" not in json.dumps(lifecycle)
    assert not activity_update(lifecycle, "agent_progress", {"kind": "model_reasoning", "text": "private"}, "thought", "now")


def test_request_hard_ceiling_metadata_preserves_content_and_idle_distinction():
    payload = {"phase": "model", "role": "builder", "model_request": 7, "model_attempt": 1,
        "elapsed_ms": 600000, "finish_reason": "HOST_ACTION_TIMEOUT", "input_tokens": None,
        "output_tokens": None, "timeout_kind": "request_wall_clock", "deadline_seconds": 600,
        "received_content": True, "idle_seconds": .2, "text": "private reasoning must not be saved"}
    row = activity_update({}, "agent_progress", payload, "timeout", "now")["model_requests"][0]
    assert row["received_content"] is True and row["idle_seconds"] == .2
    assert row["deadline_seconds"] == 600 and row["timeout_kind"] == "request_wall_clock"
    assert row["output_tokens"] is None and "private" not in json.dumps(row)


def test_native_idle_interruption_retains_attempt_diagnostics_without_faking_return():
    life = activity_update({}, "agent_progress", {"phase": "model", "role": "builder",
        "model_request": 7, "model_attempt": 1, "input_bytes": 30}, "start", "start-time")
    life.update(activity_update(life, "agent_progress", {"phase": "model_recovery", "role": "builder",
        "model_request": 7, "model_attempt": 1, "error_type": "ModelStreamStalled",
        "error_origin": "local_idle_watchdog", "stage": "mid_stream", "idle_seconds": 90,
        "received_content_chunks": 5, "first_content_seconds": 2, "retrying": True,
        "observed_response_chunks": 8, "empty_response_chunks": 3,
        "first_response_seconds": 1, "last_response_idle_seconds": 2,
        "watchdog_scope": "adapter_content", "error_chain_types": ["ModelStreamStalled"],
        "text": "private"}, "interrupted", "interrupt-time"))
    row = life["model_requests"][0]
    assert row["interrupted_at"] == "interrupt-time" and "returned_at" not in row
    assert row["stream_stage"] == "mid_stream" and row["received_content_chunks"] == 5
    assert row["error_origin"] == "local_idle_watchdog" and "private" not in json.dumps(life)
    assert row["observed_response_chunks"] == 8 and row["empty_response_chunks"] == 3
    assert row["last_response_idle_seconds"] == 2 and row["first_response_seconds"] == 1
    assert row["watchdog_scope"] == "adapter_content" and row["error_chain_types"] == ["ModelStreamStalled"]




def test_retry_countdown_preserves_native_attempt_interruption_and_diagnostics():
    payload = {"phase": "model_recovery", "role": "builder", "model_request": 9,
        "run_attempt": 0, "model_attempt": 2, "error_type": "ConnectionResetError",
        "retrying": True, "max_attempts": 6, "next_attempt": 3, "retry_delay_seconds": 4.0}
    life = activity_update({}, "agent_progress", payload, "error", "interrupted-time")
    row = life["model_requests"][0]
    assert (row["max_attempts"], row["next_attempt"], row["retry_delay_seconds"]) == (6, 3, 4.0)
    updates = activity_update(life, "agent_progress", {**payload, "phase": "model_retry_wait",
        "retry_delay_seconds": 2.0}, "wait", "later-time")
    assert updates == {}
    assert len(life["model_requests"]) == 1
    assert row["interrupted_at"] == "interrupted-time"
    assert "returned_at" not in row


def test_local_preflight_rejection_settles_native_metadata_without_provider_usage():
    started = activity_update({}, 'agent_progress', {'phase': 'model', 'role': 'builder',
        'model_request': 6, 'model_attempt': 1, 'run_attempt': 0}, 'start', 'start-time')
    row = activity_update(started, 'agent_progress', {'phase': 'model_preflight_rejected',
        'role': 'builder', 'model_request': 6, 'model_attempt': 1, 'run_attempt': 0,
        'error_type': 'AgentRuntimeError', 'context': {'estimated_input_tokens': 40000,
        'input_capacity': 39168, 'provider_dispatched': False}, 'text': 'public status'},
        'reject', 'reject-time')['model_requests'][0]
    assert row['started_at'] == 'start-time' and row['interrupted_at'] == 'reject-time'
    assert row['error_origin'] == 'local_context_preflight' and row['error_type'] == 'AgentRuntimeError'
    assert row['provider_dispatched'] is row['reasoning_available'] is row['retrying'] is False
    assert 'returned_at' not in row and 'input_tokens' not in row and 'output_tokens' not in row
