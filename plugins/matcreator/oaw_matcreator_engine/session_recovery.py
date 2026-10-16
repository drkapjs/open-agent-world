"""Close interrupted tool exchanges in retained ADK history without replay."""
from google.adk.events import Event
from google.genai import types

from .tool_execution import feedback


def repair_interrupted_tools(events):
    """Preserve original events/results and synthesize only missing responses.

    An absent response proves neither failure nor lack of side effects. This is
    done only when restoring a stopped worker, never against a live invocation.
    Call IDs are scoped to the invocation, agent and branch that issued them.
    """
    answered = {
        (event.invocation_id, event.author, event.branch, part.function_response.id)
        for event in events
        for part in (event.content.parts if event.content else []) or []
        if part.function_response is not None
    }
    repaired = []
    for event in events:
        repaired.append(event)
        missing = []
        for part in (event.content.parts if event.content else []) or []:
            call = part.function_call
            if call is None or not call.id:
                continue  # ADK assigns IDs; do not guess identities in corrupt data.
            key = (event.invocation_id, event.author, event.branch, call.id)
            if key in answered:
                continue
            missing.append(types.Part(function_response=types.FunctionResponse(
                id=call.id, name=call.name,
                response=feedback("tool_interrupted",
                    "The previous invocation ended before this tool returned a result. "
                    "Its outcome is unknown. Inspect current state and existing operation IDs before retrying; do not repeat completed work."),
            )))
            answered.add(key)
        if missing:
            repaired.append(Event(invocation_id=event.invocation_id, author=event.author,
                branch=event.branch, timestamp=event.timestamp,
                content=types.Content(role="user", parts=missing)))
    return repaired
