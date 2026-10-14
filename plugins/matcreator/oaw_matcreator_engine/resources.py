"""Worker-injected OAW resources, shared explicitly by every recursive agent.

The RPC endpoint reauthorizes each operation in the host. This module owns no
world database, Sandbox driver, credentials store or generic OAW service.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar

from google.genai import types
from .tool_execution import ResilientFunctionTool

BRIDGE_INSTRUCTION = """
All resources come from connected OAW cards. Call oaw_list_tools, then
oaw_invoke_tool with the current capability_id and exact argument schema.
The catalog's name and capability_id fields are NOT callable function names.
For example, a catalog entry named knowledge_search must be invoked as
oaw_invoke_tool(capability_id=<its returned capability_id>, arguments={...}).
Never call knowledge_search or operation:knowledge_search directly.
Tool errors are observations: correct the request or choose another approach.
For unknown execution outcomes, inspect existing operations before retrying.
Use knowledge_search / knowledge_inspect for skills, knowledge_save_memory for
reviewable experience, and returned skill_node_id values for resource scripts.
All computation must run in a connected Sandbox. Discover its paths through
inspect_sandbox; use execute_command and wait_sandbox_operation. The engine's
workspace_dir is PRIVATE orchestration metadata, never a computation directory.
Publish files using publish_artifact. Pass its oaw_artifact_ref to
submit_step_result.artifacts, never host or Sandbox paths. Retain OAW operation
IDs while work is running and inspect them before retrying; never duplicate jobs.
Use the planner/executor/subagent tools for orchestration, not a second scheduler.
For long compute, inspect_background_jobs discovers durable provider schemas.
Use submit_background_job (matcreator.bohrium-batch for Bohrium) with a stable
request_key, or attach_background_job for existing external work. Never resubmit
an unknown submission. For existing local operations, attach core.sandbox-operation
using its operation_id as external_id. The host checks jobs without model calls.
Call submit_step_result(status="waiting", job_ids=[...]) and finish the step
instead of polling for hours. Only claim completion after collecting and verifying
outputs. Agent Stop pauses automation; cancelling remote compute is a separate action.
"""

_bridge = None
_execution_context = ContextVar("matcreator_execution_context", default=None)


@contextmanager
def execution_context(session_id, node_id, attempt=None):
    token = _execution_context.set((session_id, node_id, attempt))
    try:
        yield
    finally:
        _execution_context.reset(token)


def _record_artifact(reference, *, produced=False):
    context = _execution_context.get()
    if context:
        from .agents.graph_logger import AgentGraphLogger
        AgentGraphLogger(context[0]).log_artifact(context[1], reference, produced=produced)


def configure(rpc, emit_event, contexts=None):
    """Bind once before constructing agents; each worker owns exactly one Run."""
    global _bridge
    _bridge = ResourceBridge(rpc, emit_event, contexts)


def bridge():
    if _bridge is None:
        raise RuntimeError("The OAW resource bridge must be configured before engine construction")
    return _bridge


def unpack(response):
    for item in response.get("result", []):
        if item.get("type") == "inputText":
            return json.loads(item["text"])
    return None


class ResourceBridge:
    def __init__(self, rpc, emit_event, contexts=None):
        self.rpc = rpc
        self.emit_event = emit_event
        self.verified = set()
        self.input_lock = asyncio.Lock()
        self.conversation_input = {}
        self.contexts = contexts

    async def before_model(self, callback_context, llm_request):
        # One worker hosts the native planner and recursive executors. Broadcast
        # accepted updates to each model context without replacing orchestration.
        async with self.input_lock:
            pending = await self.rpc('pending_conversation_input')
            if not isinstance(pending, list):
                raise RuntimeError('Conversation input bridge failed')
            for message in pending:
                self.conversation_input[message['id']] = message
            injected = [types.Content(role='user', parts=[types.Part.from_text(text=message['text'])])
                        for message in self.conversation_input.values()]
        llm_request.contents.extend(injected)
        # Slow summaries serialize only their own context, not parallel agents
        # or inbox reads. Acknowledge new input after preparing a usable request.
        if self.contexts is not None:
            await self.contexts.before_model(callback_context, llm_request,
                [item.model_dump(mode='json', exclude_none=True) for item in injected])
        if pending:
            result = await self.rpc('acknowledge_conversation_input', message_ids=[m['id'] for m in pending])
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise RuntimeError('Conversation input acknowledgement failed')

    async def oaw_list_tools(self) -> dict:
        """Discover currently connected OAW tools and their argument schemas."""
        result = await self.rpc("list_tools")
        if isinstance(result, dict) and result.get("ok") is False:
            return result
        if not isinstance(result, list):
            raise ValueError("Invalid OAW tool catalog response")
        return {"tools": result}

    async def oaw_invoke_tool(self, capability_id: str, arguments: dict) -> dict:
        """Invoke a currently authorized OAW tool with its exact schema."""
        context = _execution_context.get()
        attempt = context[2] if context else None
        if attempt and capability_id in {"host:submit_background_job", "host:attach_background_job"}:
            from .agents.execution_agent.recovery import prepare_background_job_request
            prefix = prepare_background_job_request(attempt)
            key = str(arguments.get("request_key", ""))
            arguments = {**arguments, "request_key": key if key.startswith(prefix) else prefix + key[:180]}
        response = await self.rpc("invoke_tool", capability_id=capability_id, arguments=arguments)
        if not response.get("ok"):
            error = unpack(response) or response.get("error")
            if isinstance(error, dict) and error.get("ok") is False:
                error = error.get("error", error)
            return {"ok": False, "error": error}
        value = unpack(response)
        if attempt and response.get("tool") in {"submit_background_job", "attach_background_job"} and isinstance(value, dict) and value.get("id"):
            from .agents.execution_agent.recovery import record_background_job_reference
            record_background_job_reference(attempt, value["id"])
        if response.get("tool") == "publish_artifact" and isinstance(value, dict) and value.get("state") == "ready":
            value = {**value, "oaw_artifact_ref": "oaw:artifact:" + arguments["collection"] + ":" + value["version_id"]}
            _record_artifact(value["oaw_artifact_ref"], produced=True)
        if response.get("tool") == "inspect_artifacts" and isinstance(value, dict) and value.get("state") == "ready":
            version = value.get("version_id") or arguments.get("version_id")
            if arguments.get("collection") and version:
                _record_artifact(f"oaw:artifact:{arguments['collection']}:{version}")
        images = []
        for item in response.get("result", []):
            if item.get("type") == "inputImage":
                import base64
                header, data = item["imageUrl"].split(",", 1)
                images.append(types.Part.from_bytes(data=base64.b64decode(data), mime_type=header[5:].split(";")[0]))
        return {"ok": True, "result": value, **({"media": images} if images else {})}


async def before_model_conversation_input(callback_context, llm_request):
    await bridge().before_model(callback_context, llm_request)


def resource_tools():
    value = bridge()
    return [ResilientFunctionTool(value.oaw_list_tools), ResilientFunctionTool(value.oaw_invoke_tool)]


async def read_artifact_input(reference):
    """Resolve and reauthorize a version before delivering metadata to an agent."""
    pieces = reference.split(":")
    tools = await bridge().rpc("list_tools")
    tool = next((item for item in tools if item["name"] == "inspect_artifacts"), None)
    if tool is None:
        raise ValueError("Artifact read access is missing or revoked")
    response = await bridge().oaw_invoke_tool(tool["capability_id"], {"collection": pieces[2], "version_id": pieces[3]})
    value = response.get("result")
    if not response.get("ok") or not isinstance(value, dict) or value.get("state") != "ready":
        raise ValueError("Input artifact is unavailable or not ready")
    # Some host versions omit the tool name in replies. Resolution itself is
    # direct access evidence and can be recorded without guessing that name.
    _record_artifact(reference)
    return value


async def validate_artifacts(references):
    value = bridge()
    for reference in references:
        if not isinstance(reference, str):
            return "Publish output via OAW and use its oaw_artifact_ref."
        pieces = reference.split(":")
        if len(pieces) != 4 or pieces[:2] != ["oaw", "artifact"] or not all(pieces[2:]):
            return "Publish output via OAW and use its oaw_artifact_ref."
        definitions = await value.rpc("list_tools")
        tool = next((t for t in definitions if t["name"] == "inspect_artifacts"), None)
        if tool is None:
            return "Artifact read access is missing or revoked."
        result = await value.rpc("invoke_tool", capability_id=tool["capability_id"],
            arguments={"collection": pieces[2], "version_id": pieces[3]})
        record = unpack(result)
        if not result.get("ok") or not isinstance(record, dict) or record.get("state") != "ready":
            return "Published artifact is unavailable or not ready."
        value.verified.add(reference)
    return None


def split_verified_artifacts(artifacts):
    verified = bridge().verified
    return ([ref for ref in artifacts if ref in verified], [ref for ref in artifacts if ref not in verified])


def emit_child_event(event):
    bridge().emit_event(event, child=True)


async def job_tool(name, arguments):
    """Use the live catalog even for engine-owned waiting and recovery actions."""
    definitions = await bridge().rpc("list_tools")
    definition = next((item for item in definitions if item["name"] == name), None)
    if definition is None:
        raise ValueError("Background job capability is unavailable or revoked")
    result = await bridge().oaw_invoke_tool(definition["capability_id"], arguments)
    if not result.get("ok"):
        raise ValueError("Background job operation was rejected; inspect current access and job state")
    return result["result"]
