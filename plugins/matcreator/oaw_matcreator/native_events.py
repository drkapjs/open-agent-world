"""Project native activity without turning internal reports into user replies."""


def is_conversation_author(event, *, child=False):
    # The Pack's planner owns user communication in both normal and Flash mode.
    # Child executors have a separate invocation even if an author name repeats.
    return not child and getattr(event, "author", None) == "thinking_agent"


def public_events(event, *, child=False):
    """Keep all tool activity; publish only the planner's non-thought text.

    The worker still persists the complete native event in its ADK session so
    internal reports remain available to the planner and checkpoint recovery.
    """
    for index, part in enumerate(getattr(getattr(event, "content", None), "parts", None) or []):
        if getattr(part, "thought", False):
            continue
        call = getattr(part, "function_call", None)
        response = getattr(part, "function_response", None)
        base = {"author": getattr(event, "author", "MatCreator"), "subagent": child}
        if call:
            yield {"type": "event", "event": "tool_started", "payload": {**base,
                "name": call.name, "call_id": call.id, "arguments": call.args}}
        if response:
            # Raw tool output belongs to the private native session.
            result = response.response or {}
            failed = result.get("ok") is False or bool(result.get("error")) or result.get("status") in (
                "error", "failed", "cancelled", "needs_replanning")
            yield {"type": "event", "event": "tool_completed", "payload": {**base,
                "name": response.name, "call_id": response.id, "ok": not failed}}
        if getattr(part, "text", None) and is_conversation_author(event, child=child):
            yield {"type": "event", "event": "agent_message", "payload": {**base,
                "text": part.text, "final": bool(event.is_final_response()),
                "provider_message_id": f"{event.id}:{index}"}}
