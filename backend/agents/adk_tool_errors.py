"""ADK dispatch failures occur before the scoped callable's tool boundary."""
from __future__ import annotations

import asyncio

from .tool_execution import execute_tool


async def on_tool_error(*, tool, args, tool_context, error):
    # ADK supplies this placeholder when lookup fails, before calling any tool.
    from google.adk.tools.base_tool import BaseTool

    if asyncio.current_task().cancelling():
        raise asyncio.CancelledError()
    if type(tool) is BaseTool and tool.description == "Tool not found":
        return {"ok": False, "error": {
            "code": "unknown_tool", "side_effects": "none",
            "message": "This tool is not registered. Use only the function names and argument schemas supplied in this request.",
        }}

    async def failed():
        raise error

    # Includes ADK argument conversion failures outside our scoped callable.
    # The common boundary sanitizes details and never retries the invocation.
    return (await execute_tool(failed)).response
