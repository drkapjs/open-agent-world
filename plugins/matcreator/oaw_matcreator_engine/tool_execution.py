"""Pack-local ADK tool boundary; no host imports, retries or Run transitions."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any, get_type_hints
from uuid import uuid4

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.function_tool import FunctionTool
from google.genai import types
from pydantic import ConfigDict, ValidationError, create_model

logger = logging.getLogger(__name__)


def feedback(code, message, *, side_effects="unknown", **details):
    return {"ok": False, "error": {
        "code": code, "message": message, "side_effects": side_effects, **details,
    }}


def execution_error(error):
    if isinstance(error, TimeoutError):
        return feedback("tool_timeout", "Tool execution timed out. Inspect the operation state before retrying; work may still be running.")
    error_id = uuid4().hex
    logger.error("MatCreator tool failed (error_id=%s)", error_id,
                 exc_info=(type(error), error, error.__traceback__))
    return feedback("tool_execution_error",
        "Tool execution failed. Check the argument schema and inspect the operation state before retrying; it may have partially completed.",
        error_id=error_id)


async def on_tool_error(*, tool, args, tool_context, error):
    if asyncio.current_task().cancelling():
        raise asyncio.CancelledError()
    if type(tool) is BaseTool and tool.description == "Tool not found":
        return feedback("unknown_tool",
            "This function is not registered. Use only the functions supplied in this request. "
            "OAW resource names and capability_id values are catalog entries, not callable functions. "
            "Call oaw_list_tools, then oaw_invoke_tool(capability_id=<returned capability_id>, arguments=<object matching its input_schema>).",
            side_effects="none")
    return execution_error(error)


def _media_json(value):
    if isinstance(value, types.Part):
        return value.model_dump(mode="json", exclude_none=True)
    raise TypeError("Tool result must contain JSON values or media Parts")


class ResilientFunctionTool(FunctionTool):
    """Contain invocation/result failures, including tool-only cancellation.

    Own and await the task. Parent cancellation must win even when a tool
    suppresses CancelledError or raises another exception during cleanup.
    """

    def __init__(self, func, **kwargs):
        super().__init__(func, **kwargs)
        hints = get_type_hints(func)
        fields = {name: (hints.get(name, Any), ... if param.default is inspect.Parameter.empty else param.default)
            for name, param in inspect.signature(func).parameters.items()
            if name not in self._ignore_params}
        self._argument_model = create_model(f"{self.name}Arguments",
            __config__=ConfigDict(strict=True, extra="forbid"), **fields)

    async def run_async(self, *, args, tool_context):
        owner = asyncio.current_task()
        if owner.cancelling():
            raise asyncio.CancelledError()
        try:
            self._argument_model.model_validate(args)
        except ValidationError as error:
            fields = [{"field": ".".join(map(str, item["loc"]))[:160], "type": item["type"]}
                for item in error.errors(include_input=False, include_context=False, include_url=False)[:10]]
            return feedback("invalid_tool_arguments", "Correct these fields using the tool's argument schema; the tool was not executed.",
                side_effects="none", fields=fields)

        async def invoke():
            try:
                result = await super(ResilientFunctionTool, self).run_async(args=args, tool_context=tool_context)
                # Validate without changing successful payloads or media objects.
                json.dumps(result, allow_nan=False, default=_media_json)
                return result
            except Exception as error:
                return execution_error(error)

        task = asyncio.create_task(invoke(), name="matcreator-tool-invocation")
        try:
            result = await task
        except asyncio.CancelledError:
            if owner.cancelling():
                raise
            return feedback("tool_cancelled", "This tool invocation was cancelled. Inspect its state before retrying.")
        if owner.cancelling():
            raise asyncio.CancelledError()
        return result
