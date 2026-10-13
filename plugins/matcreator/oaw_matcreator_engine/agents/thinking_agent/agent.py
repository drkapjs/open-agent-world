from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.adk.agents.callback_context import CallbackContext

from ...constants import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from ...adk_compat import install_lenient_tool_argument_parsing
from .planning import validate_graph, set_node_status
from .intent import validate_intent
from .summarize import validate_summarize
from .session_summary import write_session_summary
from ...workspace import get_session_workdir
from ...resources import BRIDGE_INSTRUCTION, resource_tools
from ..execution_graph_state import get_execution_graph, set_execution_graph
from .history_tools import read_session_log


logger = logging.getLogger(__name__)

_model_name = os.environ.get("LLM_MODEL", LLM_MODEL)
_model_api_key = os.environ.get("LLM_API_KEY", LLM_API_KEY)
_model_base_url = os.environ.get("LLM_BASE_URL", LLM_BASE_URL)

# Some OpenAI-compatible endpoints occasionally emit malformed JSON in a
# streamed tool call.  Retrying the LLM node is the safe recovery: attempting
# to salvage a partial/concatenated payload could execute the wrong tool args.
# Recovery is handled at the orchestrator level (see
# ``orchestrator/agent.py:_stream_planning_with_recovery``), which wraps the
# planning stream in a try/except json.JSONDecodeError — mirroring the
# execution-phase recovery.  The legacy ``RetryConfig`` did nothing here
# because ``LlmAgent`` runs through ``llm_flows``, which does not consume
# ``retry_config``.


def confirm_plan_and_start_execution(tool_context: ToolContext) -> dict:
    """Signal that the user has approved the plan and execution should begin.

    Call this when the user explicitly confirms they want to proceed with the plan
    (e.g. "yes", "proceed", "go ahead").  The orchestrator will then delegate each
    graph node to the execution agent — do NOT execute nodes yourself after calling this.
    """
    from ..cancellation import clear_cancellation

    sid = tool_context.state.get("session_id") or tool_context._invocation_context.session.id
    clear_cancellation(sid)

    # The model may retry a successful tool call before the orchestrator gets
    # control back. Confirmation must not reset nodes that already started or
    # completed during that window.
    if tool_context.state.get("execution_approved", False):
        return {
            "status": "ok",
            "message": "Execution was already approved; hand control back to the orchestrator.",
        }

    # Reset all node statuses so a fresh run starts clean
    graph = get_execution_graph(tool_context.state)
    if graph and isinstance(graph.get("nodes"), dict):
        for node in graph["nodes"].values():
            node["status"] = "pending"
            node["result"] = None
        set_execution_graph(tool_context.state, graph)

    tool_context.state["execution_approved"] = True
    tool_context.state["_node_exec_counter"] = 0
    return {
        "status": "ok",
        "message": "Execution approved. STOP making further calls.",
    }


def resume_execution(tool_context: ToolContext) -> dict:
    """Resume execution from the current graph state.

    Call this when the user explicitly requests to continue execution after an interruption.
    """
    if tool_context.state.get("execution_approved", False):
        return {
            "status": "ok",
            "message": "Execution resume was already approved; hand control back to the orchestrator.",
        }

    graph = get_execution_graph(tool_context.state)
    if not graph or not graph.get("nodes"):
        return {
            "status": "error",
            "message": "No execution_graph found in session state. Create/validate a graph first.",
        }

    pending = [nid for nid, n in graph["nodes"].items() if n.get("status") == "pending"]
    if not pending:
        return {
            "status": "error",
            "message": "No pending nodes remain — all nodes are complete or blocked.",
        }

    tool_context.state["return_to_planner"] = False
    tool_context.state["return_to_planner_reason"] = None
    tool_context.state["execution_approved"] = True

    return {
        "status": "ok",
        "message": (
            f"Execution resume approved. "
            f"The orchestrator will continue with {len(pending)} pending node(s)."
        ),
    }


# ---------------------------------------------------------------------------
# Mode helper
# ---------------------------------------------------------------------------


def _get_agent_mode(state: dict) -> str:
    """Return the active agent mode: 'flash', 'bench', or 'normal'."""
    mode = state.get("agent_mode")
    if mode in ("normal", "bench", "flash"):
        return mode
    return "bench" if state.get("benchmark_mode", False) else "normal"


# ---------------------------------------------------------------------------
# Flash mode: ad-hoc step execution
# ---------------------------------------------------------------------------


async def run_flash_step(
    action: str,
    suggested_skills: list[str],
    tool_context: ToolContext,
    label: Optional[str] = None,
) -> dict:
    """Execute a step ad-hoc. No DAG or plan required.

    Use when direct computation, scripts, or skill-level execution is needed.
    Multiple independent calls in one response turn run concurrently.

    Args:
        action: What to do (same semantics as a DAG node action).
        suggested_skills: Pass []; put OAW skill references in action.
        label: Optional display name shown in the agent graph. Reuse the SAME label
            when re-running the same logical task so any remote job it submitted is
            re-attached rather than duplicated.
    """
    from ..execution_agent.step_executor_runner import run_step_executor

    counter = tool_context.state.get("_flash_step_counter", 0) + 1
    tool_context.state["_flash_step_counter"] = counter
    # The node ID is the identity a remote job is tracked under, so it must be
    # stable across retries of the same logical task.  Deriving the fallback from
    # the action (rather than an incrementing counter) means a repeated step
    # re-attaches to its existing job instead of submitting a duplicate one.
    if label:
        node_id = label.lower().replace(" ", "_")[:40]
    else:
        node_id = f"flash_{hashlib.sha256(action.encode()).hexdigest()[:12]}"

    result = await run_step_executor(
        step_number=counter,
        action=action,
        suggested_skills=suggested_skills,
        workspace_dir="",
        prior_context=None,
        node_id=node_id,
        tool_context=tool_context,
    )
    # Surface the durable identity to transcript consumers.  In particular a
    # label-less Flash call cannot reproduce the SHA-derived id from its tool
    # arguments without hashing asynchronously in the browser.
    return {"node_id": node_id, **result}


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

_FLASH_INSTRUCTION = """You are the MatCreator research planner. Goal: {goal}
For a direct task use run_flash_step with suggested_skills=[] and explicit acceptance criteria.
Use connected OAW tools to inspect evidence. Report uncertainty and verify outputs.
"""

_NORMAL_INSTRUCTION = """You are the MatCreator research planner.
Goal: {goal}
Current execution graph: {execution_graph}
Previous findings: {summarize}
1. Understand the request; use connected knowledge tools for skills and evidence.
2. Create a DAG with validate_graph. Edges mean a predecessor must succeed before
   the successor starts; never infer data provenance from execution order. Put OAW
   skill IDs, inputs and acceptance criteria in action; use suggested_skills=[].
{confirmation_instruction}
4. The execution orchestrator dispatches the plan. Review its actual results.
5. For waiting work inspect existing OAW operations before resuming; never submit
   duplicate jobs. Use set_node_status and resume_execution only after inspection.
6. On failure, explain why a revised plan is needed and obtain its approval.
Use validate_summarize and write_session_summary to preserve verified findings.
"""

_MATCREATOR_INSTRUCTION = "{instruction_body}"

# ---------------------------------------------------------------------------
# before_agent_callback: inject dynamic context into session state
# ---------------------------------------------------------------------------

def before_agent_callback(callback_context: CallbackContext) -> None:
    """Refresh memory and state each invocation."""
    state = callback_context._invocation_context.session.state
    for key, default in [
        ("execution_graph", None),
        ("goal", None),
        ("summarize", None),
        ("trajectory_step", 0),
    ]:
        if key not in state:
            callback_context.state[key] = default

    # The worker assigns a private metadata directory for this session.
    session_id = state.get("session_id", "default")
    callback_context.state["workspace_dir"] = state.get("workdir") or str(get_session_workdir(session_id))

    agent_mode = _get_agent_mode(state)
    if agent_mode == "flash":
        callback_context.state["instruction_body"] = _FLASH_INSTRUCTION.format(
            goal=state.get("goal") or "Not set",
        )
    else:
        if agent_mode == "bench":
            confirmation_instruction = (
                "3. **Benchmark mode is active.** Immediately call `confirm_plan_and_start_execution` "
                "after `validate_graph` succeeds — do NOT wait for user confirmation."
            )
        else:
            confirmation_instruction = (
                '3. **Wait for explicit user confirmation** (e.g. "yes", "ok", "proceed") before proceeding.\n'
                "   When the user confirms, call `confirm_plan_and_start_execution` — do NOT execute nodes yourself."
            )
        callback_context.state["instruction_body"] = _NORMAL_INSTRUCTION.format(
            goal=state.get("goal") or "Not set",
            execution_graph=get_execution_graph(state),
            summarize=state.get("summarize"),
            confirmation_instruction=confirmation_instruction,
        )

    return None

# ---------------------------------------------------------------------------
# MatCreator agent instance
# ---------------------------------------------------------------------------

# Repair malformed tool-call argument JSON (unescaped inner quotes) emitted by
# some OpenAI-compatible endpoints instead of crashing the planning stream.
install_lenient_tool_argument_parsing()


thinking_agent = LlmAgent(
    name="thinking_agent",
    model=LiteLlm(model=_model_name, base_url=_model_base_url, api_key=_model_api_key),
    description="Plans research and reviews execution evidence through connected OAW tools.",
    instruction=_MATCREATOR_INSTRUCTION + "\n" + BRIDGE_INSTRUCTION,
    tools=[FunctionTool(tool) for tool in (
        validate_graph, validate_intent, validate_summarize, write_session_summary,
        confirm_plan_and_start_execution, resume_execution, set_node_status,
        run_flash_step, read_session_log,
    )] + resource_tools(),
    before_agent_callback=before_agent_callback,
)
