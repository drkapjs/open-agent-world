from __future__ import annotations

import asyncio
import logging
import json
import os
import uuid
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.adk.runners import Runner
from google.adk.agents.run_config import RunConfig
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.tools.agent_tool import ForwardingArtifactService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from ...llm_cards import LLMCard, select_executor_llm_card
from ...resources import split_verified_artifacts, emit_child_event, execution_context, read_artifact_input
from ..consumed_inputs import validate_input_references
from ...workspace import get_session_workdir
from .step_executor import (
    STEP_EXECUTOR_AGENT_NAME,
    StepExecutorInput,
    StepExecutorResult,
    build_step_executor_agent,
)
from .recovery import (
    active_remote_job_for_attempt,
    finish_node_attempt,
    heartbeat_node_attempt,
    remote_job_reference as _remote_job_reference,
    start_node_attempt,
)
from ..graph_logger import AgentGraphLogger
from ..execution_graph_state import get_execution_graph, set_execution_graph
from ..session_log import (
    append_session_log_entry,
    collect_artifact_paths,
    is_session_log_state_key,
    session_artifacts_from_state,
)
from ..cancellation import (
    is_cancellation_requested,
    get_cancellation_reason,
    is_step_cancellation_requested,
    clear_step_cancellation,
)

logger = logging.getLogger(__name__)

# Time between flag-file polls in the watcher task.
# Event-count polling is unreliable when events are sparse (e.g. mid-LLM-call),
# so we use a wall-clock interval instead.
_CANCEL_POLL_INTERVAL = 0.5  # seconds

# Wall-clock timeout for a single step or sub-step execution.
# A sub-step that exceeds this returns needs_replanning instead of hanging.
_SUB_STEP_TIMEOUT = int(os.environ.get("SUB_STEP_TIMEOUT", "3600"))  # seconds
# One-time bounded extension granted when the step timeout fires while a tracked
# remote job is still active.  This is deliberately modest: the durable job store
# and the remote job monitor — not a long-lived LLM session — are what keep a
# remote job alive, so the extension only avoids churn for jobs about to settle.
_REMOTE_JOB_GRACE_TIMEOUT = int(os.environ.get("STEP_REMOTE_JOB_GRACE_TIMEOUT", "300"))  # seconds
_RECOVERY_HEARTBEAT_INTERVAL = int(os.environ.get("STEP_RECOVERY_HEARTBEAT_INTERVAL", "10"))  # seconds
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response_dict(response) -> dict:
    """Best-effort conversion for ADK function response payloads."""
    if isinstance(response, dict):
        return response
    try:
        return dict(response or {})
    except (TypeError, ValueError):
        return {}


def _append_unique(values: list[str], value) -> None:
    if isinstance(value, str) and value and value not in values:
        values.append(value)


def _build_step_content(step_input: StepExecutorInput, *, llm_card: LLMCard, workspace_dir: Path, state):
    # Images arrive from authorized OAW tools, never from model-supplied host paths.
    return types.Content(role="user", parts=[types.Part.from_text(
        text=step_input.model_dump_json(exclude_none=True))]), []


def _verify_step_result_artifacts(
    result: StepExecutorResult,
    additional_artifacts: Optional[list[str]] = None,
) -> tuple[StepExecutorResult, list[str]]:
    """Prevent successful steps from claiming nonexistent output artifacts.

    Only artifact references inspected through the live OAW capability bridge
    are accepted; local paths never serve as evidence.
    """
    existing_artifacts, missing_artifacts = split_verified_artifacts(result.artifacts)
    result.artifacts = existing_artifacts

    if additional_artifacts:
        _, additional_missing_artifacts = split_verified_artifacts(additional_artifacts)
        for artifact in additional_missing_artifacts:
            if artifact not in missing_artifacts:
                missing_artifacts.append(artifact)

    if result.status == "success" and missing_artifacts:
        missing_text = ", ".join(missing_artifacts)
        result.status = "needs_replanning"
        result.replan_reason = (
            "Step reported success with output references not verified through OAW: "
            f"{missing_text}"
        )
        logger.warning("[step_executor_runner] %s", result.replan_reason)
        result.concise_summary = result.replan_reason
        result.key_results = result.replan_reason

    return result, missing_artifacts


async def _watch_for_cancellation(
    target: asyncio.Task,
    session_id: str,
    step_number: int,
) -> None:
    """Poll cancellation flags at fixed intervals; call target.cancel() when flagged.

    Runs concurrently with the step execution task.  Using task.cancel() raises
    CancelledError at the target's current await point (including in-flight HTTP
    calls to the LLM), which is the only reliable way to stop a running coroutine.
    """
    try:
        while not target.done():
            await asyncio.sleep(_CANCEL_POLL_INTERVAL)
            if (
                is_cancellation_requested(session_id)
                or is_step_cancellation_requested(session_id, step_number)
            ):
                logger.warning(
                    "[CANCEL] Step %d watcher triggered task cancellation (session=%s)",
                    step_number, session_id,
                )
                target.cancel()
                return
    except asyncio.CancelledError:
        pass


async def _heartbeat_recovery_attempt(attempt: dict) -> None:
    try:
        while True:
            await asyncio.sleep(_RECOVERY_HEARTBEAT_INTERVAL)
            await asyncio.to_thread(heartbeat_node_attempt, attempt)
    except asyncio.CancelledError:
        pass


def _mark_node_waiting_on_remote_job(
    tool_context: ToolContext,
    *,
    node_id: Optional[str],
    remote_job: dict,
    summary: str,
) -> None:
    """Record the remote-job handoff directly on the execution graph node.

    Writing the node state here — instead of relying on the orchestrator LLM to
    call ``set_node_status`` — keeps the handoff deterministic, and preserves the
    job identity the next executor needs in order to re-attach.
    """
    if not node_id:
        return
    graph_state = get_execution_graph(tool_context.state) or {}
    graph_nodes = graph_state.get("nodes") or {}
    if node_id not in graph_nodes:
        return
    graph_nodes[node_id]["status"] = "waiting"
    graph_nodes[node_id]["result"] = summary
    graph_nodes[node_id]["remote_job"] = remote_job
    graph_nodes[node_id]["recovery"] = {
        "status": "waiting_remote",
        "recorded_at": _now(),
    }
    set_execution_graph(tool_context.state, graph_state)


def _graph_dependency_step_ids(
    graph: AgentGraphLogger,
    tool_context: ToolContext,
    node_id: Optional[str],
) -> list[str]:
    """Resolve direct execution-DAG predecessors to logged graph node IDs.

    The execution container is the visual parent only for root DAG nodes.
    Dependents must be attached to their *declared* predecessors, not inferred
    later from the order in which they happened to start.
    """
    if not node_id:
        return []
    execution_graph = get_execution_graph(tool_context.state) or {}
    predecessors = [
        edge[0]
        for edge in execution_graph.get("edges") or []
        if isinstance(edge, (list, tuple))
        and len(edge) == 2
        and edge[1] == node_id
        and isinstance(edge[0], str)
    ]
    if not predecessors:
        return []

    execution_id = tool_context.state.get("_graph_exec_node_id", "orchestrator")
    resolved = []
    for predecessor_id in predecessors:
        # In the ordinary case the predecessor belongs to this execution
        # phase.  The lookup also handles a remote-job resume that starts a
        # fresh execution container after its predecessor completed earlier.
        step_id = (execution_graph.get("nodes", {}).get(predecessor_id) or {}).get("attempt_id")
        if not step_id or graph.node(step_id) is None:
            step_id = graph.find_step_node(predecessor_id, execution_graph.get("graph_id"),
            None if execution_graph.get("graph_id") else execution_id)
        if not step_id:
            logger.warning(
                "[step_executor_runner] missing logged predecessor %s for %s",
                predecessor_id,
                node_id,
            )
            continue
        if step_id not in resolved:
            resolved.append(step_id)
    return resolved


def _remote_job_prior_context(tool_context: ToolContext, node_id: Optional[str]) -> Optional[str]:
    """Return re-attachment instructions when a node already owns a remote job.

    Injected into the executor's ``prior_context`` so re-attachment does not
    depend on the planner remembering the job, nor on the submission idempotency
    key happening to match.
    """
    if not node_id:
        return None
    node = ((get_execution_graph(tool_context.state) or {}).get("nodes") or {}).get(node_id)
    if not isinstance(node, dict):
        return None
    remote_job = node.get("remote_job")
    if not isinstance(remote_job, dict) or not remote_job.get("job_id"):
        return None
    background_note = (
        " A background command may still be in flight; inspect it through connected "
        "OAW tools before starting a new one."
        if remote_job.get("has_background_command")
        else ""
    )
    return (
        "REMOTE JOB ALREADY SUBMITTED for this step: "
        f"job_id={remote_job['job_id']} provider={remote_job.get('provider')} "
        f"external_id={remote_job.get('external_id')} last_known_status={remote_job.get('status')}. "
        "This is a legacy job reference. Discover connected OAW tools and inspect the "
        "existing job using its external identity; never submit a duplicate. "
        "If it is still running or no authorized tool can inspect it, report "
        "needs_replanning with that limitation and preserve its identity. "
        f"Do not claim automatic re-attachment or start a replacement.{background_note}"
    )


async def _await_step_completion(
    inner_task: asyncio.Task,
    recovery_attempt: dict,
    *,
    step_number: int,
    session_id: str,
) -> tuple[bool, Optional[dict]]:
    """Wait for the executor task, honouring one remote-job grace extension.

    Returns ``(timed_out, active_remote_job)``.  ``active_remote_job`` is the
    still-active tracked job that caused the final timeout, if any; the caller
    treats that case as a handoff rather than a step failure.  Unlike
    ``asyncio.wait_for`` this never cancels ``inner_task`` on timeout, so the
    grace window can reuse the same in-flight executor.
    """
    timeout = float(_SUB_STEP_TIMEOUT)
    grace_remaining = float(max(_REMOTE_JOB_GRACE_TIMEOUT, 0))
    while True:
        done, _ = await asyncio.wait({inner_task}, timeout=timeout)
        if done:
            return False, None

        remote_job = await asyncio.to_thread(active_remote_job_for_attempt, recovery_attempt)
        if remote_job is None:
            inner_task.cancel()
            return True, None
        if grace_remaining <= 0:
            inner_task.cancel()
            return True, remote_job

        logger.info(
            "[TIMEOUT] Step %d reached its timeout with remote job %s still %s; "
            "granting a single %ds grace window (session=%s)",
            step_number, remote_job["job_id"], remote_job["status"], int(grace_remaining), session_id,
        )
        timeout, grace_remaining = grace_remaining, 0.0


async def _cleanup_step_runner(
    runner: Runner,
    tasks: tuple[asyncio.Task, ...],
) -> None:
    """Wait for executor tasks to finish, then close their runner."""
    await asyncio.gather(*tasks, return_exceptions=True)
    try:
        await asyncio.wait_for(runner.close(), timeout=5.0)
    except Exception:
        pass


def _log_cancelled_cleanup_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("[CANCEL] step runner cleanup failed")


def _schedule_step_runner_cleanup(
    runner: Runner,
    tasks: tuple[asyncio.Task, ...],
) -> None:
    cleanup_task = asyncio.create_task(_cleanup_step_runner(runner, tasks))
    cleanup_task.add_done_callback(_log_cancelled_cleanup_result)


async def _stream_step_events(
    runner: Runner,
    session,
    content,
    step_id: str,
    tool_context: ToolContext,
    graph: AgentGraphLogger,
    captured: Optional[tuple] = None,
    run_config=None,
) -> tuple[dict, list[str], list[str], dict]:
    """Stream events from the step executor sub-agent and collect results.

    Returns (step_state_delta, plot_paths, artifact_paths, event_log). Raises
    CancelledError if the task is cancelled mid-stream.
    """
    step_state_delta: dict = captured[0] if captured else {}
    pending_tool_calls: dict[str, dict] = {}
    plot_paths: list[str] = captured[1] if captured else []
    artifact_paths: list[str] = captured[2] if captured else []
    event_log: dict = captured[3] if captured else {"conversation": [], "tool_calls": []}

    async with aclosing(
        runner.run_async(
            user_id=session.user_id, session_id=session.id, new_message=content, run_config=run_config
        )
    ) as agen:
        async for event in agen:
            # Retain provider termination metadata, never infer timeout from an
            # empty result. Partial captures also survive task cancellation.
            diagnostics = event_log.setdefault("diagnostics", {})
            for name in ("finish_reason", "error_code"):
                value = getattr(event, name, None)
                if value is not None:
                    diagnostics[name] = str(getattr(value, "value", value))[:160]
            diagnostics["last_event_id"] = str(getattr(event, "id", ""))
            emit_child_event(event)
            if event.actions and event.actions.state_delta:
                tool_context.state.update(event.actions.state_delta)
                step_state_delta.update(event.actions.state_delta)

            if event.content:
                for part in event.content.parts:
                    fc = getattr(part, "function_call", None)
                    fr = getattr(part, "function_response", None)
                    is_thought = getattr(part, "thought", False)
                    text = getattr(part, "text", None)

                    if is_thought and text:
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "thought",
                            "content": text,
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)
                    elif text and not fc and not fr:
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "text",
                            "content": text,
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)
                    elif fc and not is_thought:
                        pending_tool_calls[fc.id or fc.name] = {
                            "name": fc.name,
                            "call_id": fc.id,
                            "input": dict(fc.args or {}),
                            "args_summary": str(dict(fc.args or {}))[:300],
                            "start_time": _now(),
                        }
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "function_call",
                            "content": f"{fc.name}({str(dict(fc.args or {}))[:500]})",
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)
                    elif fr:
                        response = _response_dict(fr.response)
                        _append_unique(plot_paths, response.get("plot_path"))
                        for artifact_path in collect_artifact_paths(response):
                            _append_unique(artifact_paths, artifact_path)

                        record = pending_tool_calls.pop(fr.id or fr.name, {"name": fr.name, "call_id": fr.id, "start_time": _now()})
                        record["output"] = response
                        record["result_summary"] = str(fr.response)[:300]
                        record["end_time"] = _now()
                        record["artifacts"] = collect_artifact_paths(response)
                        event_log["tool_calls"].append(record.copy())
                        await asyncio.to_thread(graph.log_tool_call, step_id, record)
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "function_response",
                            "content": f"{fr.name} → {str(fr.response)[:500]}",
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)

            if step_state_delta.get("_step_result"):
                break  # The validated result is the completion boundary.

    return step_state_delta, plot_paths, artifact_paths, event_log


async def _run_step_with_result_repair(runner, agent, session, content, step_id, tool_context, graph, captured):
    await _stream_step_events(runner, session, content, step_id, tool_context, graph, captured)
    if not captured[0].get("_step_result"):
        # One bounded repair turn may only report the evidence already obtained.
        # Removing resource/child tools prevents accidental side-effect replay.
        original_tools = agent.tools
        original_schema = agent.input_schema
        agent.tools = [tool for tool in original_tools if getattr(tool, "name", None) == "submit_step_result"]
        agent.input_schema = None
        captured[3].setdefault("diagnostics", {})["result_repair_attempted"] = True
        try:
            repair = types.Content(role="user", parts=[types.Part.from_text(text=
                "Your execution stream ended without submit_step_result. This is not evidence of a timeout. "
                "Call submit_step_result now using only observed evidence. Do not repeat or invent operations. "
                "If incomplete, report needs_replanning and the observed blocker; retain known job references.")])
            async with asyncio.timeout(60):
                await _stream_step_events(runner, session, repair, step_id, tool_context, graph, captured,
                    run_config=RunConfig(max_llm_calls=2))
        except (TimeoutError, LlmCallsLimitExceededError) as error:
            captured[3]["diagnostics"]["result_repair_exhausted"] = type(error).__name__
        finally:
            agent.tools = original_tools
            agent.input_schema = original_schema
    return captured


MAX_RECURSION_DEPTH = 3


async def run_step_executor(
    step_number: int,
    action: str,
    suggested_skills: list[str],
    workspace_dir: str,
    prior_context: Optional[str] = None,
    node_id: Optional[str] = None,
    consumed_inputs: Optional[list[dict]] = None,
    *,
    tool_context: ToolContext,
) -> dict:
    """Run step_executor as an isolated sub-agent, following AgentTool logic.

    Each invocation gets its own workspace subdirectory. When called from within
    a step_executor (recursion_depth > 0) the workspace is nested under the
    parent's workspace; at depth 0 it lives under the session workdir.
    """
    session_id = tool_context.state.get("session_id", "default")
    recursion_depth = tool_context.state.get("recursion_depth", 0)
    scheduled_node = bool(node_id and tool_context.state.get("_graph_execution_id") and recursion_depth == 0)

    if recursion_depth >= MAX_RECURSION_DEPTH:
        return {
            "status": "error",
            "message": (
                f"Maximum recursion depth ({MAX_RECURSION_DEPTH}) reached. "
                "Execute this action directly or call submit_step_result with needs_replanning."
            ),
        }

    # Use node_id for label when provided (DAG mode); fall back to step_number.
    effective_id = node_id if node_id else str(step_number)

    # Deterministic re-attachment: a node that already owns a tracked remote job
    # must poll it rather than submit a replacement sandbox.
    reattach_context = _remote_job_prior_context(tool_context, node_id) if scheduled_node else None
    if reattach_context:
        prior_context = f"{reattach_context}\n\n{prior_context}" if prior_context else reattach_context

    # Metadata stays in the host-assigned session directory.
    step_workspace = get_session_workdir(session_id)
    step_workspace.mkdir(parents=True, exist_ok=True)
    output_dir = None  # Output bytes are published through OAW, never written here.
    logger.debug("[step_executor_runner] depth=%d node %s workspace: %s", recursion_depth, effective_id, step_workspace)

    graph = AgentGraphLogger(session_id)
    parent_id = tool_context.state.get("_graph_exec_node_id", "orchestrator")
    step_id = f"step_{uuid.uuid4().hex}"
    plan = get_execution_graph(tool_context.state) or {}
    plan_id = tool_context.state.get("_graph_plan_id", plan.get("graph_id"))
    previous_id = graph.find_step_node(str(effective_id), plan_id, None if scheduled_node and plan_id else parent_id)
    previous = graph.node(previous_id) if previous_id else None
    retry_of = previous_id if previous and previous.get("status") in {"failed", "cancelled", "needs_replanning", "waiting"} else None
    parent_path = tool_context.state.get("_step_label_path", "")
    step_label_path = f"{parent_path}-{effective_id}" if parent_path else effective_id
    llm_card = select_executor_llm_card(
        action=action,
        suggested_skills=suggested_skills,
        prior_context=prior_context,
    )
    public_llm_card = llm_card.public_dict()
    logger.info(
        "[step_executor_runner] node %s using LLM card %s (%s)",
        effective_id,
        llm_card.name,
        llm_card.model,
    )
    dependency_ids = _graph_dependency_step_ids(graph, tool_context, node_id) if scheduled_node else []
    await asyncio.to_thread(
        graph.log_node_start,
        step_id,
        "step",
        f"Node {step_label_path}",
        parent_id,
        dependency_ids=dependency_ids,
        plan_id=plan_id,
        execution_id=tool_context.state.get("_graph_execution_id"),
        logical_node_id=str(effective_id),
        retry_of=retry_of,
        actor_id=step_id,
        actor_label=f"Executor {step_label_path}",
        workstream_id=f"{plan_id}:{effective_id}" if scheduled_node and plan_id else f"{parent_id}:subtask:{effective_id}",
    )
    # Record the observed launch, separately from scheduler/data dependencies.
    # Only roots need a planning fork; later steps already name their recorded
    # predecessors. Never manufacture a project merge on task completion.
    launch_source = plan.get("planning_id") if scheduled_node else parent_id
    if launch_source and not dependency_ids and not retry_of:
        await asyncio.to_thread(graph.log_task_fork, launch_source, step_id)
    if scheduled_node and node_id in plan.get("nodes", {}):
        plan["nodes"][node_id].update(status="running", attempt_id=step_id)
        set_execution_graph(tool_context.state, plan)

    deliveries = []
    try:
        references = validate_input_references(consumed_inputs or [], graph, plan if plan_id else None, node_id)
        for reference in references:
            if reference.get("artifact_ref"):
                with execution_context(session_id, step_id):
                    value = await read_artifact_input(reference["artifact_ref"])
                deliveries.append({**reference, "value": value})
            else:
                source_id = reference.get("step_id") or plan.get("nodes", {}).get(reference["node_id"], {}).get("attempt_id")
                if not source_id and reference.get("node_id"):
                    source_id = graph.find_step_node(reference["node_id"], plan_id)
                source = graph.node(source_id) if source_id else None
                if not source or source.get("status") != "success":
                    raise ValueError("Input source has no successful recorded attempt in this plan")
                deliveries.append({"step_id": source_id, "value": source.get("result") or {
                    "concise_summary": source.get("summary"), "artifacts": source.get("artifacts", [])}})
        if deliveries:
            delivered_text = "Explicit input results:\n" + json.dumps(deliveries, ensure_ascii=False)
            prior_context = f"{prior_context}\n\n{delivered_text}" if prior_context else delivered_text
    except ValueError as exc:
        graph.log_node_complete(step_id, "failed", summary=str(exc))
        return {"status": "error", "step_id": step_id, "message": str(exc)}

    # Serialize input as user message (matches AgentTool input_schema path)
    step_input = StepExecutorInput(
        step_number=step_number,
        action=action,
        suggested_skills=suggested_skills,
        workspace_dir=str(step_workspace),
        output_dir=str(output_dir) if output_dir else None,
        prior_context=prior_context,
    )
    content, input_image_paths = _build_step_content(
        step_input,
        llm_card=llm_card,
        workspace_dir=step_workspace,
        state=tool_context.state,
    )

    # Log input parameters
    await asyncio.to_thread(graph.log_node_input, step_id, {
        "node_id": effective_id,
        "step_number": step_number,
        "action": action,
        "workspace_dir": str(step_workspace),
        "output_dir": str(output_dir) if output_dir else None,
        "prior_context": prior_context,
        "suggested_skills": suggested_skills,
        "llm_card": public_llm_card,
        "input_images": input_image_paths,
        "consumed_inputs": references,
    })
    step_input_log = {
        "node_id": effective_id,
        "step_id": step_id,
        "parent_id": parent_id,
        "step_number": step_number,
        "action": action,
        "workspace_dir": str(step_workspace),
        "output_dir": str(output_dir) if output_dir else None,
        "prior_context": prior_context,
        "suggested_skills": suggested_skills,
        "llm_card": public_llm_card,
        "input_images": input_image_paths,
    }
    append_session_log_entry(tool_context, {
        "kind": "step_start",
        **step_input_log,
    })
    recovery_attempt = await asyncio.to_thread(
        start_node_attempt,
        workspace_dir=step_workspace,
        session_id=session_id,
        node_id=str(effective_id),
        step_id=step_id,
        step_number=step_number,
        action=action,
        suggested_skills=suggested_skills,
        prior_context=prior_context,
        graph_id=plan_id,
        recovery_scope=parent_id if not scheduled_node else None,
        scheduler_node=scheduled_node,
    )
    if scheduled_node:
        graph_state = get_execution_graph(tool_context.state) or {}
        graph_nodes = graph_state.get("nodes") or {}
        if node_id in graph_nodes:
            graph_nodes[node_id]["status"] = "running"
            graph_nodes[node_id]["attempt_id"] = step_id
            graph_nodes[node_id]["recovery"] = {
                "status": "running",
                "started_at": _now(),
            }
            set_execution_graph(tool_context.state, graph_state)

    # Pre-step cancellation check — abort before creating the runner if flagged
    if is_cancellation_requested(session_id) or is_step_cancellation_requested(session_id, step_number):
        reason = get_cancellation_reason(session_id) or "user_requested"
        logger.warning(
            "[CANCEL] Node %s aborted before start (session=%s, reason=%s)",
            effective_id, session_id, reason,
        )
        await asyncio.to_thread(graph.log_node_complete, step_id, "cancelled", summary=f"Cancelled before start: {reason}")
        clear_step_cancellation(session_id, step_number)
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "cancelled",
            "message": f"Node {effective_id} skipped: execution cancellation was requested ({reason}).",
        })
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="cancelled",
            message=f"Node {effective_id} skipped: execution cancellation was requested ({reason}).",
        )
        return {
            "status": "cancelled",
            "step_id": step_id,
            "message": f"Node {effective_id} skipped: execution cancellation was requested ({reason}).",
        }
    # Create runner with isolated session (mirrors AgentTool)
    invocation_context = tool_context._invocation_context
    child_app_name = (
        invocation_context.app_name if invocation_context else STEP_EXECUTOR_AGENT_NAME
    )
    step_executor_agent = build_step_executor_agent(llm_card)
    runner = Runner(
        app_name=child_app_name,
        agent=step_executor_agent,
        artifact_service=ForwardingArtifactService(tool_context),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        credential_service=invocation_context.credential_service,
    )

    # Inherit parent context, pin private metadata storage and increment depth.
    state_dict = {
        k: v
        for k, v in tool_context.state.to_dict().items()
        if not k.startswith("_adk") and not is_session_log_state_key(k)
    }
    state_dict["workspace_dir"] = str(step_workspace)
    state_dict["step_number"] = step_number
    state_dict["output_dir"] = None
    state_dict["session_output_dir"] = None
    state_dict["recursion_depth"] = recursion_depth + 1
    state_dict["_graph_exec_node_id"] = step_id
    state_dict["_step_label_path"] = step_label_path
    state_dict["_step_result"] = None

    session = await runner.session_service.create_session(
        app_name=child_app_name,
        user_id=invocation_context.user_id,
        state=state_dict,
    )

    # Wrap event streaming in a Task so task.cancel() can raise CancelledError at
    # the current await point (including in-flight LLM HTTP calls).  A sibling
    # watcher task polls cancellation flags on a wall-clock interval and cancels
    # the inner task when a flag is detected.
    captured = ({}, [], [], {"conversation": [], "tool_calls": []})

    async def stream_with_context():
        with execution_context(session_id, step_id, recovery_attempt):
            for delivery in deliveries:
                if delivery.get("step_id"):
                    graph.log_data_delivery(delivery["step_id"], step_id)
            return await _run_step_with_result_repair(runner, step_executor_agent, session, content,
                step_id, tool_context, graph, captured)

    inner_task = asyncio.create_task(stream_with_context())
    watcher = asyncio.create_task(
        _watch_for_cancellation(inner_task, session_id, step_number)
    )
    recovery_heartbeat = asyncio.create_task(_heartbeat_recovery_attempt(recovery_attempt))

    cancelled = False
    timed_out = False
    waiting_remote_job: Optional[dict] = None
    runner_error: Optional[Exception] = None
    step_state_delta, plot_paths, artifact_paths, event_log = captured
    try:
        timed_out, waiting_remote_job = await _await_step_completion(
            inner_task,
            recovery_attempt,
            step_number=step_number,
            session_id=session_id,
        )
        if not timed_out:
            step_state_delta, plot_paths, artifact_paths, event_log = inner_task.result()
    except asyncio.CancelledError:
        cancelled = True
    except Exception as exc:
        runner_error = exc
    finally:
        if not watcher.done():
            watcher.cancel()
        if not recovery_heartbeat.done():
            recovery_heartbeat.cancel()
        cleanup_tasks = (inner_task, watcher, recovery_heartbeat)
        if cancelled:
            _schedule_step_runner_cleanup(runner, cleanup_tasks)
        else:
            await _cleanup_step_runner(runner, cleanup_tasks)

    if timed_out and waiting_remote_job is not None:
        job_summary = (
            f"Step {step_number} reached its {_SUB_STEP_TIMEOUT}s executor timeout while tracked "
            f"{waiting_remote_job['provider']} job {waiting_remote_job['job_id']} is still "
            f"{waiting_remote_job['status']}. The executor was released; the job keeps running."
        )
        logger.warning("[WAITING] %s (session=%s)", job_summary, session_id)
        remote_job_reference = _remote_job_reference(waiting_remote_job)
        _mark_node_waiting_on_remote_job(
            tool_context,
            node_id=node_id if scheduled_node else None,
            remote_job=remote_job_reference,
            summary=job_summary,
        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id, "waiting", summary=job_summary,
        )
        clear_step_cancellation(session_id, step_number)
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "waiting",
            "message": job_summary,
            "remote_job": remote_job_reference,
            "events": event_log,
        }, artifacts=artifact_paths)
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="waiting",
            artifacts=artifact_paths,
            message=job_summary,
        )
        return {
            "status": "waiting",
            "step_id": step_id,
            "remote_job": remote_job_reference,
            "message": job_summary,
        }

    if timed_out and recovery_attempt.get("background_jobs"):
        from ...resources import job_tool
        identities = recovery_attempt["background_jobs"]
        await job_tool("wait_background_jobs", {"job_ids": identities})
        payload = StepExecutorResult(status="waiting", job_ids=identities,
            concise_summary="Executor released; OAW continues monitoring the existing jobs.").model_dump()
        await asyncio.to_thread(finish_node_attempt, recovery_attempt, status="waiting", result=payload)
        await asyncio.to_thread(graph.log_node_complete, step_id, "waiting", summary=payload["concise_summary"])
        return {**payload, "step_id": step_id}

    if timed_out:
        logger.warning(
            "[TIMEOUT] Step %d timed out after %ds (session=%s)",
            step_number, _SUB_STEP_TIMEOUT, session_id,
        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id, "needs_replanning",
            summary=f"Timed out after {_SUB_STEP_TIMEOUT}s",
        )
        clear_step_cancellation(session_id, step_number)
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "needs_replanning",
            "replan_reason": f"Step {step_number} timed out after {_SUB_STEP_TIMEOUT}s.",
            "events": event_log,
        }, artifacts=artifact_paths)
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="needs_replanning",
            artifacts=artifact_paths,
            message=f"Step {step_number} timed out after {_SUB_STEP_TIMEOUT}s.",
        )
        return {
            "status": "needs_replanning",
            "step_id": step_id,
            "failure_kind": "executor_timeout",
            "replan_reason": f"Step {step_number} timed out after {_SUB_STEP_TIMEOUT}s.",
        }

    if cancelled:
        reason = get_cancellation_reason(session_id) or "user_requested"
        logger.warning(
            "[CANCEL] Step %d task cancelled (session=%s, reason=%s)",
            step_number, session_id, reason,
        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id, "cancelled", summary=f"Cancelled mid-step ({reason})"
        )
        clear_step_cancellation(session_id, step_number)
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "cancelled",
            "message": f"Step {step_number} cancelled ({reason}).",
            "events": event_log,
        }, artifacts=artifact_paths)
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="cancelled",
            artifacts=artifact_paths,
            message=f"Step {step_number} cancelled ({reason}).",
        )
        return {
            "status": "cancelled",
            "step_id": step_id,
            "message": f"Step {step_number} cancelled ({reason}).",
        }

    if runner_error is not None:
        error_message = f"Step executor failed unexpectedly: {runner_error}"
        logger.error(
            "[step_executor_runner] node %s failed unexpectedly: %s",
            effective_id,
            runner_error,
            exc_info=(type(runner_error), runner_error, runner_error.__traceback__),
        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id,
            "failed",
            summary=error_message,
        )
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "failed",
            "message": error_message,
            "events": event_log,
        }, artifacts=artifact_paths)
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="failed",
            artifacts=artifact_paths,
            message=error_message,
        )
        clear_step_cancellation(session_id, step_number)
        return {
            "status": "error",
            "step_id": step_id,
            "failure_kind": "executor_error",
            "message": error_message,
        }

    if step_state_delta:
        await asyncio.to_thread(graph.log_state_delta, step_id, step_state_delta)
    public_state_delta = {
        key: value for key, value in step_state_delta.items()
        if not key.startswith("_")
    }

    step_result_data = step_state_delta.get("_step_result")
    if step_result_data:
        tool_context.state["_step_result"] = None  # State has no pop(); reset instead
        result = StepExecutorResult.model_validate(step_result_data)
        result, missing_artifacts = _verify_step_result_artifacts(
            result,

            additional_artifacts=[*artifact_paths, *plot_paths],
        )
        skill_refresh = None
        if result.status == "success":
            if skill_refresh and skill_refresh["status"] == "error":
                refresh_error = skill_refresh["message"]
                result.status = "needs_replanning"
                result.replan_reason = refresh_error
                result.key_results = refresh_error
                result.concise_summary = refresh_error
        # Do not persist or return unverified tool outputs.  In particular,
        # this prevents a stale plot_path from being rendered as a broken image
        # while the step is being replanned.
        artifact_paths, _ = split_verified_artifacts(
            artifact_paths,

        )
        plot_paths, _ = split_verified_artifacts(
            plot_paths,

        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id,
            result.status,
            summary=result.concise_summary,
            artifacts=result.artifacts,
        )
        payload = result.model_dump(exclude_none=True)
        if result.status == "waiting":
            from .recovery import record_background_job_reference
            for identity in result.job_ids:
                record_background_job_reference(recovery_attempt, identity)
        payload["step_id"] = step_id
        await asyncio.to_thread(graph.log_result, step_id, payload)
        if skill_refresh:
            payload["skill_registry_refresh"] = skill_refresh
        if missing_artifacts:
            payload["missing_artifacts"] = missing_artifacts
            payload["message"] = result.replan_reason
        if plot_paths:
            payload["plot_paths"] = plot_paths
            payload["plot_path"] = plot_paths[0]
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": result.status,
            "result": payload,
            "state_delta": public_state_delta,
            "events": event_log,
        }, artifacts=[*artifact_paths, *result.artifacts, *plot_paths])
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status=result.status,
            result=payload,
            artifacts=[*artifact_paths, *result.artifacts, *plot_paths],
        )
        clear_step_cancellation(session_id, step_number)
        return payload

    # submit_step_result was never called — surface as replanning signal
    logger.warning("[step_executor_runner] step %d: submit_step_result was never called", step_number)
    result = StepExecutorResult(
        status="needs_replanning",
        failure_kind="result_missing",
        replan_reason="step executor did not call submit_step_result — no result captured",
    )
    await asyncio.to_thread(graph.log_node_complete, step_id, "needs_replanning")
    append_session_log_entry(tool_context, {
        "kind": "step_complete",
        **step_input_log,
        "status": "needs_replanning",
        "result": result.model_dump(),
        "state_delta": public_state_delta,
        "events": event_log,
    }, artifacts=artifact_paths)
    await asyncio.to_thread(
        finish_node_attempt,
        recovery_attempt,
        status="needs_replanning",
        result=result.model_dump(),
        artifacts=artifact_paths,
    )
    clear_step_cancellation(session_id, step_number)
    return {**result.model_dump(), "step_id": step_id, "diagnostics": event_log.get("diagnostics", {})}
