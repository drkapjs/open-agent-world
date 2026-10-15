"""Observe an already-admitted Sandbox operation without replaying it."""
from pydantic import BaseModel, ConfigDict
from backend.plugins.background_jobs import BackgroundJobProvider, JobObservation


class ExistingOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def inspect(context, operation_id):
    value = await context.inspect_operation(operation_id)
    state = value["state"]
    if state == "running":
        status = "running"
    elif state == "interrupted":
        status = "interrupted"
    elif value.get("cancelled") or state == "cancelled":
        status = "cancelled"
    elif value.get("timed_out") or state in {"error", "timed_out"}:
        status = "failed"
    elif state == "finished" and value.get("operation_kind") == "python_install":
        status = "succeeded"
    elif value.get("exit_code", value.get("tool_result", {}).get("exit_code")) == 0:
        status = "succeeded"
    else:
        status = "failed"
    return JobObservation(status=status, detail="Inspect the existing operation result and published outputs; never resubmit based on elapsed time.")


definition = BackgroundJobProvider(specification=ExistingOperation, inspect=inspect, poll_interval_seconds=5)
