"""Planning-Execution Orchestrator for MatCreator.

Every invocation runs a planning-first loop:

  1. Planning phase  — always runs first. planning_agent (thinking_agent) handles
                       user intent and sets state["execution_approved"] = True
                       when the user-approved DAG should run. If the flag is not
                       set the turn was conversational; the loop exits and control
                       returns to the user.

  2. Execution phase — delegates the full plan to execution_agent (an LlmAgent that
                       spawns isolated step_executor sub-agents and may run steps in
                       parallel). Terminates early if execution_agent calls `to_planner`
                       (sets state["return_to_planner"] = True).
                       After all steps complete (or on early exit), loops back to
                       the planning phase within the same invocation.

"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import aclosing
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from ...workspace import get_workspace_root, init_session_workdir
from ..graph_logger import AgentGraphLogger
from ..cancellation import clear_cancellation
from ..execution_agent.recovery import reconcile_recovery_state
from ..execution_graph_state import get_execution_graph

logger = logging.getLogger(__name__)

_PLANNING_NODE_STATE_KEY = "_graph_planning_node_id"
_EXECUTION_STREAM_ATTEMPTS = max(
    1, int(os.environ.get("MATCREATOR_EXECUTION_JSON_RETRY_ATTEMPTS", "2"))
)
_PLANNING_STREAM_ATTEMPTS = max(
    1, int(os.environ.get("MATCREATOR_PLANNER_JSON_RETRY_ATTEMPTS", "2"))
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_agent_mode(state: dict) -> str:
    """Return the active agent mode: 'flash', 'bench', or 'normal'."""
    mode = state.get("agent_mode")
    if mode in ("normal", "bench", "flash"):
        return mode
    return "bench" if state.get("benchmark_mode", False) else "normal"


def _validate_graph_ready(state: dict) -> tuple[bool, str]:
    """Return (ready, reason) — ready=True when at least one pending node exists."""
    graph = get_execution_graph(state)
    if not graph or not isinstance(graph, dict):
        return False, "No execution_graph in session state."
    nodes = graph.get("nodes") or {}
    if not nodes:
        return False, "Execution graph has no nodes."
    pending = [nid for nid, n in nodes.items() if n.get("status") == "pending"]
    if not pending:
        return False, f"No pending nodes (all {len(nodes)} node(s) are complete or blocked)."
    return True, f"{len(pending)} pending node(s) ready."


def _is_graph_complete(state: dict) -> bool:
    """Return True when every node in the graph reached 'success'."""
    nodes = (get_execution_graph(state) or {}).get("nodes") or {}
    return bool(nodes) and all(n.get("status") == "success" for n in nodes.values())


def _waiting_for_external_work(state):
    from ..thinking_agent.planning import ready_nodes_from_graph
    graph = get_execution_graph(state) or {}
    nodes = list(graph.get("nodes", {}).values())
    return (any(node.get("status") == "waiting" and node.get("background_jobs") for node in nodes)
        and not any(node.get("status") in {"running", "failed"} for node in nodes)
        and not ready_nodes_from_graph(graph))


def _get_planning_node_id(state: dict, graph: AgentGraphLogger) -> str:
    """Allocate an invocation identity; the underlying planner is still shared."""
    node_id = f"planning_{uuid.uuid4().hex}"
    state[_PLANNING_NODE_STATE_KEY] = node_id
    return node_id


def _raise_model_error(event: Event) -> None:
    """ADK can end a stream with an error event instead of raising.

    Stream exhaustion alone is not successful completion. Do not retry an
    entire agent invocation here: earlier tools may already have side effects.
    The caller yields the event first so its diagnostics remain checkpointed.
    """
    reason = getattr(event, "finish_reason", None)
    reason = str(getattr(reason, "value", reason) or "")
    code = str(getattr(event, "error_code", None) or "")
    role = {"thinking_agent": "planner", "execution_orchestrator": "execution orchestrator"}.get(
        getattr(event, "author", None), "agent")
    if reason == "MAX_TOKENS" or code == "MAX_TOKENS":
        raise RuntimeError(
            f"MatCreator {role} reached the model output token limit (MAX_TOKENS) "
            "before completing its response. Increase maximum output tokens in Settings / Models "
            "before resuming. Execution history is retained; tools have not been automatically retried."
        )
    if code or reason in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "MALFORMED_FUNCTION_CALL"}:
        # Provider error messages can contain request details. Keep them in the
        # native checkpoint; publish only a bounded, non-sensitive diagnosis.
        raise RuntimeError(f"MatCreator {role} model response failed before completion. "
            "Execution history is retained; inspect the model connection before resuming.")


async def _stream_execution_with_recovery(
    execution_agent: BaseAgent,
    ctx: InvocationContext,
    *,
    max_attempts: int = _EXECUTION_STREAM_ATTEMPTS,
) -> AsyncGenerator[Event, None]:
    """Run an execution stream across recoverable model-output failures.

    Step executors persist their result before returning it to the execution
    orchestrator. If the orchestrator's next tool call contains malformed JSON,
    restart the orchestration stream after folding those durable results into
    the graph. The restarted agent therefore schedules only unfinished nodes.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(1, max_attempts + 1):
        try:
            async with aclosing(execution_agent.run_async(ctx)) as execution_events:
                async for event in execution_events:
                    yield event
                    _raise_model_error(event)
                    if ctx.session.state.get("return_to_planner", False):
                        # Persist/deliver the tool response, then close this
                        # stream before another executor model/tool round. The
                        # planner owns any subsequent request for user input.
                        return
                    if _waiting_for_external_work(ctx.session.state):
                        return  # No extra model round can reinterpret a durable wait.
            return
        except json.JSONDecodeError:
            history = AgentGraphLogger(ctx.session.id)
            previous = ctx.session.state.get("_graph_exec_node_id")
            if previous and history.node(previous):
                history.log_node_complete(previous, "failed", summary="Malformed execution tool arguments")
                if attempt < max_attempts:
                    replacement = f"execution_{uuid.uuid4().hex}"
                    old = history.node(previous)
                    history.log_node_start(replacement, "execution", old["label"], old.get("parent_id"),
                        plan_id=old.get("plan_id"), execution_id=replacement, retry_of=previous)
                    ctx.session.state["_graph_exec_node_id"] = replacement
                    ctx.session.state["_graph_execution_id"] = replacement
            recovered = reconcile_recovery_state(
                ctx.session.state,
                ctx.session.state.get("workdir") or get_workspace_root(),
            )
            logger.warning(
                "[orchestrator] malformed execution tool arguments on attempt %d/%d; "
                "recovered state: %s",
                attempt,
                max_attempts,
                recovered,
                exc_info=True,
            )
            if attempt == max_attempts:
                raise


async def _stream_planning_with_recovery(
    planning_agent: BaseAgent,
    ctx: InvocationContext,
    *,
    max_attempts: int = _PLANNING_STREAM_ATTEMPTS,
) -> AsyncGenerator[Event, None]:
    """Run a planning stream across recoverable model-output failures.

    Mirrors ``_stream_execution_with_recovery`` but for the planning phase.
    Some OpenAI-compatible endpoints (e.g. GLM-5.3) occasionally emit a tool
    call whose ``function.arguments`` is not valid JSON; ADK's LiteLlm adapter
    raises ``json.JSONDecodeError`` from ``_parse_tool_call_arguments`` during
    response finalization.  Unlike the execution phase there is no durable
    sub-step state to reconcile — the planner has no persisted partial results
    — so recovery is simply re-invoking the planning agent after the failed
    stream is closed.

    Events already yielded to the client before the failure are not "unsent";
    the retried stream produces a fresh, complete set of events.  The
    ``execution_approved`` flag is reset before each retry because a
    JSONDecodeError during tool-call finalization means the tool never
    executed (so the flag was never set by the tool itself).
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    state = ctx.session.state
    for attempt in range(1, max_attempts + 1):
        state["execution_approved"] = False
        try:
            async with aclosing(planning_agent.run_async(ctx)) as planning_events:
                async for event in planning_events:
                    yield event
                    _raise_model_error(event)
                    if state.get("execution_approved", False):
                        logger.info("[orchestrator] approval received; ending planning phase")
                        break
            return
        except json.JSONDecodeError:
            history = AgentGraphLogger(ctx.session.id)
            previous = state.get(_PLANNING_NODE_STATE_KEY)
            if previous and history.node(previous):
                history.log_node_complete(previous, "failed", summary="Malformed planning tool arguments")
                if attempt < max_attempts:
                    replacement = _get_planning_node_id(state, history)
                    history.log_node_start(replacement, "planning", "Planning", "orchestrator", retry_of=previous)
                    state["_graph_exec_node_id"] = replacement
            logger.warning(
                "[orchestrator] malformed planning tool arguments on attempt %d/%d; "
                "retrying planning phase",
                attempt,
                max_attempts,
                exc_info=True,
            )
            if attempt == max_attempts:
                raise


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PlanningExecutionOrchestrator(BaseAgent):
    """Orchestrates the planning → execution loop.

    Attributes:
        planning_agent: Handles user intent, plan creation, and approval signalling.
        execution_agent: Executes one plan step at a time (skill load + tool calls).
    """

    planning_agent: BaseAgent
    execution_agent: BaseAgent

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state

        if not state.get("session_workdir_initialized"):
            workdir = init_session_workdir(ctx.session.id)
            state["session_id"] = ctx.session.id
            state["workdir"] = str(workdir)
            state["session_workdir_initialized"] = True

        graph = AgentGraphLogger(ctx.session.id)
        graph.mark_running_nodes_failed("Previous worker stopped before this activity completed")
        graph.log_node_start("orchestrator", "orchestrator", "Orchestrator")

        # Cheap and idempotent: repair the in-memory graph from durable step
        # attempt records before planning/execution resumes after a crash.
        recovered = reconcile_recovery_state(state, state.get("workdir") or get_workspace_root())
        from ..execution_agent.recovery import reconcile_background_jobs
        await reconcile_background_jobs(state)
        resume_job = state.pop("_background_job_notification", None)
        current_plan = get_execution_graph(state) or {}
        resume_existing = (bool(resume_job) and state.get("_approved_graph_id") == current_plan.get("graph_id")
            and any(resume_job in node.get("background_jobs", []) and node.get("status") == "pending"
                    for node in current_plan.get("nodes", {}).values()))
        if recovered:
            logger.warning("[orchestrator] recovered execution state: %s", recovered)

        loop_idx = graph.count_nodes_of_type("execution")

        while True:
            planning_id = _get_planning_node_id(state, graph)
            # ── Planning phase (always runs first) ───────────────────────────
            # Tools launched by the planning/O side (notably Flash steps) are
            # direct orchestrator work. Clear a previous execution round's
            # container before entering planning so those steps do not appear
            # to belong to a stale E node after replanning.
            state["_graph_exec_node_id"] = planning_id
            state["_graph_execution_id"] = None
            state["_graph_plan_id"] = None
            state["execution_approved"] = False
            logger.info("[orchestrator] entering planning phase")
            graph.log_node_start(planning_id, "planning", "Planning", "orchestrator",
                replan_of=state.get("_graph_replan_source"), actor_id="planning", actor_label="Planner", workstream_id="planning")
            state["_graph_replan_source"] = None
            # Approval is a hard handoff boundary. Yield the successful tool
            # response first so clients can persist/render it, then close the
            # planner stream before it can start another model/tool round.
            # Wrapped in _stream_planning_with_recovery to catch JSONDecodeError
            # from malformed tool-call arguments emitted by some OpenAI-compatible
            # endpoints (e.g. GLM-5.3) and retry the planning phase.
            if resume_existing:
                # Only a host notification for this still-current approved plan
                # may bypass fresh approval. Existing jobs are inspected first.
                state["execution_approved"] = True
                resume_existing = False
            else:
                async for event in _stream_planning_with_recovery(self.planning_agent, ctx):
                    yield event
            planning_id = state[_PLANNING_NODE_STATE_KEY]
            graph.log_node_complete(planning_id, "success")

            # Flash mode: thinking agent handles everything; skip execution phase
            if _get_agent_mode(state) == "flash":
                graph.log_node_complete("orchestrator", "success")
                break

            execution_approved: bool = state.get("execution_approved", False)

            # ── Execution phase ───────────────────────────────────────────────
            if execution_approved:
                ready, reason = _validate_graph_ready(state)
                if not ready:
                    logger.warning("[orchestrator] execution approved but: %s", reason)
                    state["execution_approved"] = False
                    continue  # loop back to planner

                execution_graph = get_execution_graph(state) or {}
                state["_approved_graph_id"] = execution_graph.get("graph_id")
                total_nodes = len(execution_graph.get("nodes") or {})
                pending_count = sum(
                    1 for n in execution_graph.get("nodes", {}).values()
                    if n.get("status") == "pending"
                )
                logger.info(
                    "[orchestrator] delegating %d/%d pending nodes to execution_orchestrator",
                    pending_count, total_nodes,
                )

                exec_id = f"execution_{loop_idx}"
                graph.log_node_start(
                    exec_id,
                    "execution",
                    f"Execution {loop_idx + 1}",
                    planning_id,
                    # This remains metadata: all execution nodes continue to
                    # point directly at the original planner. The frontend
                    # uses it to place one planning round on one vine layer.
                    batch_id=f"{planning_id}:round:{loop_idx}",
                    plan_id=execution_graph.get("graph_id"),
                    execution_id=exec_id,
                )
                state["_graph_exec_node_id"] = exec_id
                state["_graph_execution_id"] = exec_id
                state["_graph_plan_id"] = execution_graph.get("graph_id")

                async for event in _stream_execution_with_recovery(self.execution_agent, ctx):
                    yield event
                exec_id = state["_graph_exec_node_id"]

                if _waiting_for_external_work(state):
                    graph.log_node_complete(exec_id, "waiting", summary="OAW is monitoring existing background work.")
                    graph.log_node_complete("orchestrator", "waiting")
                    state["return_to_planner"] = False
                    state["return_to_planner_reason"] = None
                    state["execution_approved"] = False
                    state["current_step"] = None
                    break

                interrupted = state.get("return_to_planner", False)
                if interrupted:
                    reason = state.get("return_to_planner_reason", "unspecified")
                    logger.info(
                        "[orchestrator] execution interrupted — returning to planner (reason: %s)",
                        reason,
                    )
                    graph.log_node_complete(exec_id, "failed", summary=f"Interrupted: {reason}")
                    state["_graph_replan_source"] = exec_id
                    state["return_to_planner"] = False
                    state["return_to_planner_reason"] = None
                    clear_cancellation(state.get("session_id", ""))
                    if "cancel" in reason:
                        logger.warning(
                            "[CANCEL COMPLETE] Execution fully stopped for session %s — returning to planner",
                            state.get("session_id", ""),
                        )
                elif _is_graph_complete(state):
                    logger.info("[orchestrator] all %d nodes complete", total_nodes)
                    graph.log_node_complete(exec_id, "success")
                    state["_node_exec_counter"] = 0

                else:
                    logger.info("[orchestrator] execution returned with incomplete graph")
                    graph.log_node_complete(
                        exec_id,
                        "needs_replanning",
                        summary="Execution paused with graph nodes still pending, running, or waiting.",
                    )
                    state["_graph_replan_source"] = exec_id

                state["execution_approved"] = False
                state["current_step"] = None
                loop_idx += 1
                continue  # loop back to planner

            # ── No flag set — planner handled the turn conversationally ───────
            # Exit the loop; in benchmark mode the planning agent is instructed to
            # call confirm_plan_and_start_execution directly, so no fallback needed.
            graph.log_node_complete("orchestrator", "success")
            break
