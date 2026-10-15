from __future__ import annotations

import json
import logging
from typing import List, Literal, Optional

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from ...tool_execution import ResilientFunctionTool, on_tool_error
from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from ...llm_cards import LLMCard
from ...resources import BRIDGE_INSTRUCTION, resource_tools, validate_artifacts, before_model_conversation_input

logger = logging.getLogger(__name__)

STEP_EXECUTOR_AGENT_NAME = "step_executor"

# ADK's LiteLlm adapter parses streamed function-call arguments as JSON.  Some
# OpenAI-compatible endpoints occasionally finish a stream with malformed tool
# arguments, which escapes as JSONDecodeError.  Recovery is handled at the
# orchestrator level (see ``orchestrator/agent.py:_stream_execution_with_recovery``
# and ``_stream_planning_with_recovery``), which wraps agent streams in
# try/except json.JSONDecodeError.  The legacy ``RetryConfig`` did nothing
# here because ``LlmAgent`` runs through ``llm_flows``, which does not
# consume ``retry_config``; LiteLLM's own HTTP retry setting only covers
# transport/status failures.


class StepExecutorInput(BaseModel):
    step_number: int = Field(description="1-based index of this step in the plan")
    action: str = Field(description="Action description from the plan step")
    suggested_skills: List[str] = Field(description="Legacy field; pass [] and describe OAW skill references in action")
    workspace_dir: str = Field(description="Private orchestration metadata directory, never a computation directory")
    output_dir: Optional[str] = Field(
        default=None,
        description="Legacy field; outputs must be published through OAW tools",
    )
    prior_context: Optional[str] = Field(
        default=None,
        description="Condensed summaries of prior completed steps for context",
    )


class StepExecutorResult(BaseModel):
    status: Literal["success", "needs_replanning", "waiting"]
    job_ids: list[str] = Field(default_factory=list, max_length=50)
    failure_kind: Optional[Literal["executor_timeout", "executor_error", "result_missing", "blocked"]] = None
    key_results: str = Field(
        default="",
        description="Bullet-point list of key findings, values, and produced files",
    )
    artifacts: list[str] = Field(
        default_factory=list,
        description="Published OAW artifact references",
    )

    @field_validator("artifacts", mode="before")
    @classmethod
    def _coerce_artifacts(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
            return [v] if v else []
        return v
    concise_summary: str = Field(
        default="",
        description="Short user-facing paragraph describing what was done",
    )
    replan_reason: Optional[str] = Field(
        default=None,
        description="Why replanning is needed (only set when status=needs_replanning)",
    )

    @model_validator(mode="after")
    def _fill_missing_fields(self) -> "StepExecutorResult":
        if self.status == "waiting" and not self.job_ids:
            raise ValueError("Waiting requires durable OAW job_ids")
        # If the LLM returned only one of the two summary fields, mirror it to the other.
        if not self.key_results and self.concise_summary:
            self.key_results = self.concise_summary
        elif not self.concise_summary and self.key_results:
            self.concise_summary = self.key_results
        return self


_STEP_EXECUTOR_INSTRUCTION = """Execute one research step in an isolated session.
Use OAW knowledge_search/knowledge_inspect to select skills. All computation and
files belong to connected Sandboxes. Use run_sub_agent for bounded subtasks;
dispatch independent subtasks concurrently, await prerequisites for dependent work.
Keep operation IDs and inspect running jobs before retrying. Publish outputs with
publish_artifact and use its oaw_artifact_ref in submit_step_result. A success claim
requires evidence and verification. If blocked, submit needs_replanning with a
specific reason. Never treat a successful model response as scientific validation.
Report results and artifact references to your caller through submit_step_result.
Only the planner communicates with the user; return needed decisions as
needs_replanning rather than asking the user or sending conversation messages.
Always call submit_step_result before ending. A missing result, a model output
limit, a missing dependency and an execution timeout are different outcomes.
Do not claim a computation ran or a remote fleet is empty without tool evidence.
""" + BRIDGE_INSTRUCTION


async def run_sub_agent(
    step_number: int,
    action: str,
    suggested_skills: List[str],
    tool_context: ToolContext,
    prior_context: Optional[str] = None,
    consumed_inputs: Optional[List[dict]] = None,
) -> dict:
    """Spawn a child step executor to handle a sub-task.

    Call once per sub-task. For truly independent sub-tasks, issue multiple
    `run_sub_agent` calls in a SINGLE response turn — the runtime executes them
    concurrently. For dependent sub-tasks, call sequentially and pass the returned
    result's `step_id` through consumed_inputs=[{"step_id": "..."}] to the next call.

    Returns a result dict with keys: status, key_results, concise_summary, artifacts.
    If status is "cancelled", call submit_step_result with needs_replanning immediately.

    Args:
        step_number: Sub-task index (1-based, unique within this step)
        action: What the sub-task should do
        suggested_skills: Pass []; use OAW knowledge tools and include skill references in action
        prior_context: Summary of earlier sub-tasks' results (for sequential chains)
    """
    from .step_executor_runner import run_step_executor  # lazy import avoids circular dep

    result = await run_step_executor(
        step_number=step_number,
        action=action,
        suggested_skills=suggested_skills,
        workspace_dir="",  # computed internally by run_step_executor
        prior_context=prior_context,
        consumed_inputs=consumed_inputs,
        tool_context=tool_context,
    )
    if result.get("step_id") and tool_context.state.get("_graph_exec_node_id"):
        from ..graph_logger import AgentGraphLogger
        AgentGraphLogger(tool_context.state.get("session_id", "default")).log_data_delivery(
            result["step_id"], tool_context.state["_graph_exec_node_id"], evidence="child_result_returned")
    return result


async def submit_step_result(
    status: str,
    key_results: str,
    concise_summary: str,
    tool_context: ToolContext,
    artifacts: Optional[List[str]] = None,
    replan_reason: Optional[str] = None,
    job_ids: Optional[List[str]] = None,
) -> dict:
    """Submit the result of this step execution.

    Call this tool when you have finished executing the step. Replaces writing
    a JSON response. If validation fails, fix the reported fields and retry.

    Args:
        status: "success", "needs_replanning", or "waiting" on durable jobs
        job_ids: Durable OAW job IDs; required for waiting, never external CLI IDs
        key_results: Bullet-point list of key findings and produced files
        concise_summary: Short user-facing paragraph describing what was done
        artifacts: Published oaw:artifact references (empty list if none)
        replan_reason: Why replanning is needed (only when status=needs_replanning)
    """
    error = await validate_artifacts(artifacts or [])
    if error:
        return {"status": "error", "message": error}
    try:
        result = StepExecutorResult(
            status=status,  # type: ignore[arg-type]
            key_results=key_results,
            concise_summary=concise_summary,
            artifacts=artifacts or [],
            replan_reason=replan_reason,
            job_ids=job_ids or [],
        )
        if result.status == "waiting":
            from ...resources import job_tool
            try:
                await job_tool("wait_background_jobs", {"job_ids": result.job_ids})
            except ValueError as exc:
                return {"status": "error", "message": str(exc)}
        tool_context.state["_step_result"] = result.model_dump()
        return {"status": "ok", "message": "Step result submitted successfully."}
    except ValidationError as exc:
        return {
            "status": "error",
            "errors": exc.errors(),
            "message": "Validation failed. Fix the errors and call submit_step_result again.",
        }


def build_step_executor_agent(llm_card: LLMCard) -> LlmAgent:
    """Every recursive child receives the same live, authorized resource bridge."""
    return LlmAgent(
        name=STEP_EXECUTOR_AGENT_NAME,
        model=LiteLlm(model=llm_card.model, base_url=llm_card.base_url, api_key=llm_card.api_key),
        description="Executes a research step with connected OAW resources.",
        instruction=_STEP_EXECUTOR_INSTRUCTION,
        input_schema=StepExecutorInput,
        before_model_callback=before_model_conversation_input,
        tools=[ResilientFunctionTool(run_sub_agent), ResilientFunctionTool(submit_step_result), *resource_tools()],
        on_tool_error_callback=on_tool_error,
    )
