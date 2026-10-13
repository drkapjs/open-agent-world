"""Pack engine construction and bounded execution-graph projection."""
import json


def read_agent_graph(path):
    """None means an unavailable read, not an authoritative empty graph.

    Upstream writes this file in place. Reading during a subagent's write must
    not publish an empty projection that erases the last complete view.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = data.get('nodes') if isinstance(data, dict) else None
        if not isinstance(nodes, dict) or any(not isinstance(node, dict) or not node.get('id') for node in nodes.values()):
            return None
        return [{key: node.get(key) for key in ("id", "type", "label", "status", "parent_id", "summary", "dependency_ids")}
                for node in nodes.values()]
    except (OSError, ValueError):
        return None



def install(rpc, emit_event):
    from oaw_matcreator_engine.resources import configure
    configure(rpc, emit_event)
    from oaw_matcreator_engine.agent import app
    return app
