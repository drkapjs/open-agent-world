"""Real ADK dispatch must feed tool errors back into the same model loop."""
import asyncio

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from backend.agents import AgentConfig, GoogleAdkAgentRuntime
from backend.runs import InvocationCaller, InvocationContext, RuntimeInput
from backend.tests.test_agent_runtime import MutableCapabilityProvider


def context():
    return InvocationContext(run_id="run", agent_id="agent", parent_run_id=None,
        root_run_id="run", caller=InvocationCaller("test"), context_id=None,
        task_id=None, runtime_provider_id="google.adk")


def call(name, ident, args):
    return types.Part(function_call=types.FunctionCall(name=name, id=ident, args=args))


@pytest.mark.asyncio
async def test_unknown_names_and_missing_arguments_allow_correction_without_replay(monkeypatch):
    provider = MutableCapabilityProvider()
    rounds = []

    class Model(BaseLlm):
        model: str = "test"

        async def generate_content_async(self, llm_request, stream=False):
            responses = {p.function_response.id: p.function_response.response
                for c in llm_request.contents for p in c.parts or [] if p.function_response}
            rounds.append(responses)
            if len(rounds) == 1:
                parts = [call("replace_notes", "good", {"content": "first"}),
                    call("knowledge_search", "bad1", {}), call("operation:inspect_sandbox", "bad2", {})]
            elif len(rounds) == 2:
                assert responses["good"] == {"content": "first"}
                for ident in ("bad1", "bad2"):
                    assert responses[ident]["error"]["code"] == "unknown_tool"
                    assert responses[ident]["error"]["side_effects"] == "none"
                parts = [call("replace_notes", "missing", {})]
            elif len(rounds) == 3:
                assert responses["missing"].get("error")
                parts = [call("replace_notes", "corrected", {"content": "second"})]
            else:
                assert len(rounds) == 4
                assert responses["corrected"] == {"content": "second"}
                parts = [types.Part(text="Recovered.")]
            yield LlmResponse(content=types.Content(role="model", parts=parts))

    runtime = GoogleAdkAgentRuntime(provider)
    monkeypatch.setattr(runtime, "_adk_model", lambda _: Model())
    config = AgentConfig("agent", "Test")
    await runtime.create_agent(config)
    async with asyncio.timeout(10):
        events = [e async for e in runtime.execute(config, context(), RuntimeInput("test"))]
    assert events[-1].run_status == "succeeded"
    assert [args["content"] for _, _, args in provider.invocations] == ["first", "second"]
    started = {e.payload["call_id"] for e in events if e.type.value == "tool_started"}
    finished = {e.payload["call_id"] for e in events if e.type.value == "tool_completed"}
    assert started == finished == {"good", "bad1", "bad2", "missing", "corrected"}


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup", ["normal", "suppress", "error"])
async def test_dispatch_recovery_does_not_swallow_run_stop(monkeypatch, cleanup):
    entered, cleaned = asyncio.Event(), asyncio.Event()
    provider = MutableCapabilityProvider()

    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if cleanup == "suppress":
                return {"ok": True}
            if cleanup == "error":
                raise RuntimeError("private cleanup failure")
            raise
        finally:
            cleaned.set()

    provider.invoke_tool = blocked

    class Model(BaseLlm):
        model: str = "test"

        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(content=types.Content(role="model", parts=[
                call("replace_notes", "blocked", {"content": "test"})]))

    runtime = GoogleAdkAgentRuntime(provider)
    monkeypatch.setattr(runtime, "_adk_model", lambda _: Model())
    config = AgentConfig("agent", "Test")
    await runtime.create_agent(config)
    events = []

    async def run():
        async for event in runtime.execute(config, context(), RuntimeInput("test")):
            events.append(event)

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert cleaned.is_set()
    assert not any(e.type.value == "agent_completed" for e in events)


@pytest.mark.asyncio
async def test_model_failure_outside_tool_dispatch_remains_a_runtime_failure(monkeypatch):
    class Model(BaseLlm):
        model: str = "test"

        async def generate_content_async(self, llm_request, stream=False):
            raise RuntimeError("model transport unavailable")
            yield  # Keep the model interface an async generator.

    runtime = GoogleAdkAgentRuntime(MutableCapabilityProvider())
    monkeypatch.setattr(runtime, "_adk_model", lambda _: Model())
    config = AgentConfig("agent", "Test")
    await runtime.create_agent(config)
    with pytest.raises(RuntimeError, match="model transport unavailable"):
        async for _ in runtime.execute(config, context(), RuntimeInput("test")):
            pass
