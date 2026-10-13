"""Pack engine construction and bounded execution-graph projection."""
import json

_NODE_FIELDS = ("id", "type", "label", "status", "parent_id", "summary", "dependency_ids", "batch_id",
    "plan_id", "execution_id", "logical_node_id", "sequence", "attempt", "retry_of", "replan_of",
    "start_time", "end_time", "artifacts", "produced_artifacts", "accessed_artifacts", "revision", "actor_id", "actor_label", "workstream_id")


def _public_node(node):
    result = {key: node.get(key) for key in _NODE_FIELDS if key in node}
    for key in ("summary", "label"):
        if isinstance(result.get(key), str):
            result[key] = result[key][:20_000]
    task_input = node.get("input") if isinstance(node.get("input"), dict) else {}
    if isinstance(task_input.get("action"), str):
        result["action"] = task_input["action"][:20_000]
    calls = []
    for ordinal, call in enumerate(node.get("tool_calls") or []):
        if not isinstance(call, dict) or not call.get("name"):
            continue
        output = call.get("output") if isinstance(call.get("output"), dict) else {}
        calls.append({"ordinal": ordinal, "name": str(call["name"])[:500],
            "start_time": call.get("start_time"), "end_time": call.get("end_time"),
            "status": "failed" if output.get("error") or output.get("status") in ("error", "failed") else "completed"})
    if calls:
        result["tool_calls"] = calls
    return result


def _public_plan(plan):
    return {"graph_id": plan.get("graph_id"), "planning_id": plan.get("planning_id"),
        "created_at": plan.get("created_at"), "edges": plan.get("edges", []),
        "nodes": {key: {name: (value[:20_000] if isinstance(value, str) else value)
            for name, value in node.items() if name in {"node_id", "label", "action", "status", "result", "consumed_inputs", "attempt_id"}}
            for key, node in plan.get("nodes", {}).items()}}


def read_agent_history(path):
    """None means an unavailable read, not an authoritative empty graph.

    Legacy writers could write in place. Invalid history is unavailable data.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = data.get('nodes') if isinstance(data, dict) else None
        if not isinstance(nodes, dict) or any(not isinstance(node, dict) or not node.get('id') for node in nodes.values()):
            return None
        return {"schema_version": data.get("schema_version", 1), "revision": data.get("revision", 0),
            "updated_at": data.get("updated_at"), "session_id": data.get("session_id"),
            "nodes": {key: _public_node(node) for key, node in nodes.items()},
            "edges": [{key: value for key, value in edge.items()
                if key in {"id", "from", "to", "kind", "evidence", "artifact_ref"}} for edge in data.get("edges", [])],
            "plans": {key: _public_plan(plan) for key, plan in data.get("plans", {}).items()}}
    except (OSError, ValueError):
        return None


def read_agent_graph(path):
    """Compatibility adapter for callers that only need node records."""
    history = read_agent_history(path)
    return list(history["nodes"].values()) if history is not None else None



def install(rpc, emit_event):
    from oaw_matcreator_engine.resources import configure
    configure(rpc, emit_event)
    from oaw_matcreator_engine.agent import app
    return app
