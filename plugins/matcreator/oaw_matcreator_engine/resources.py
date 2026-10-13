"""Worker-injected OAW resources, shared explicitly by every recursive agent.

The RPC endpoint reauthorizes each operation in the host. This module owns no
world database, Sandbox driver, credentials store or generic OAW service.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar

from google.adk.tools.function_tool import FunctionTool
from google.genai import types

BRIDGE_INSTRUCTION = """
All resources come from connected OAW cards. Call oaw_list_tools, then
oaw_invoke_tool with the current capability_id and exact argument schema.
Use knowledge_search / knowledge_inspect for skills, knowledge_save_memory for
reviewable experience, and returned skill_node_id values for resource scripts.
All computation must run in a connected Sandbox. Discover its paths through
inspect_sandbox; use execute_command and wait_sandbox_operation. The engine's
workspace_dir is PRIVATE orchestration metadata, never a computation directory.
Publish files using publish_artifact. Pass its oaw_artifact_ref to
submit_step_result.artifacts, never host or Sandbox paths. Retain OAW operation
IDs while work is running and inspect them before retrying; never duplicate jobs.
Use the planner/executor/subagent tools for orchestration, not a second scheduler.
"""

_bridge = None
_execution_context = ContextVar("matcreator_execution_context", default=None)


@contextmanager
def execution_context(session_id, node_id):
    token = _execution_context.set((session_id, node_id))
    try:
        yield
    finally:
        _execution_context.reset(token)


def _record_artifact(reference, *, produced=False):
    context = _execution_context.get()
    if context:
        from .agents.graph_logger import AgentGraphLogger
        AgentGraphLogger(context[0]).log_artifact(context[1], reference, produced=produced)


def configure(rpc, emit_event):
    """Bind once before constructing agents; each worker owns exactly one Run."""
    global _bridge
    _bridge = ResourceBridge(rpc, emit_event)


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
    def __init__(self, rpc, emit_event):
        self.rpc = rpc
        self.emit_event = emit_event
        self.verified = set()

    async def oaw_list_tools(self) -> dict:
        """Discover currently connected OAW tools and their argument schemas."""
        return {"tools": await self.rpc("list_tools")}

    async def oaw_invoke_tool(self, capability_id: str, arguments: dict) -> dict:
        """Invoke a currently authorized OAW tool with its exact schema."""
        response = await self.rpc("invoke_tool", capability_id=capability_id, arguments=arguments)
        if not response.get("ok"):
            return {"ok": False, "error": unpack(response) or response.get("error")}
        value = unpack(response)
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


def resource_tools():
    value = bridge()
    return [FunctionTool(value.oaw_list_tools), FunctionTool(value.oaw_invoke_tool)]


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
