"""Agent execution graph logger.

Writes a JSON file per session to {workspace_root}/agent_graphs/{session_id}.json
tracking the agent node hierarchy and execution status for frontend visualization.

Schema
------
{
  "session_id": "<str>",
  "nodes": {
    "<node_id>": {
      "id": "<str>",
      "type": "orchestrator|planning|execution|tester|step",
      "label": "<str>",
      "status": "idle|running|success|failed|needs_replanning",
      "parent_id": "<str|null>",
      "dependency_ids": ["<str>", ...],
      "batch_id": "<str|null>",
      "start_time": "<ISO-8601|null>",
      "end_time": "<ISO-8601|null>",
      "summary": "<str|null>",
      "artifacts": ["<str>", ...],
      "input": {"step_number": int, "action": str, ...} | null,
      "tool_calls": [{"name": str, "input": dict, "output": dict, "args_summary": str, "result_summary": str,
                       "start_time": str, "end_time": str}, ...],
      "state_delta": {"<key>": "<value>", ...}
    }
  },
  "edges": [{"from": "<str>", "to": "<str>"}],
  "updated_at": "<ISO-8601>"
}
"""

from __future__ import annotations

import json
import os
import uuid
import threading
import time
from datetime import datetime, timezone
from typing import List, Literal, Optional

from ..workspace import ADK_DIR

_session_locks: dict[str, threading.Lock] = {}
_session_locks_mutex = threading.Lock()

NodeStatus = Literal["idle", "pending", "running", "waiting", "success", "failed", "cancelled", "needs_replanning", "blocked"]
NodeType = Literal["orchestrator", "planning", "execution", "tester", "step"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentGraphLogger:
    """File-backed logger that tracks agent node lifecycle for a single session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        graph_dir = ADK_DIR / "agent_graphs"
        graph_dir.mkdir(parents=True, exist_ok=True)
        self._path = graph_dir / f"{session_id}.json"
        with _session_locks_mutex:
            if session_id not in _session_locks:
                _session_locks[session_id] = threading.Lock()
            self._lock = _session_locks[session_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_node_start(
        self,
        node_id: str,
        node_type: NodeType,
        label: str,
        parent_id: Optional[str] = None,
        batch_id: Optional[str] = None,
        dependency_ids: Optional[List[str]] = None,
        *,
        plan_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        logical_node_id: Optional[str] = None,
        retry_of: Optional[str] = None,
        replan_of: Optional[str] = None,
        actor_id: Optional[str] = None,
        actor_label: Optional[str] = None,
        workstream_id: Optional[str] = None,
    ) -> None:
        """Create an immutable activity identity with status=running.

        ``parent_id`` records the phase that launched a node.  When a DAG
        node has explicit predecessors, ``dependency_ids`` supplies its real
        control edges. Containment never creates a dependency edge.
        """
        with self._lock:
            graph = self._read()
            existing = graph["nodes"].get(node_id)
            dependencies = [value for value in dict.fromkeys(dependency_ids or []) if value in graph["nodes"]]
            # IDs identify activities, not reusable agent instances. Idempotent
            # start notifications must never erase a completed attempt's evidence.
            if existing and node_type != "orchestrator":
                return
            sequence = (existing or {}).get("sequence") or graph.get("next_sequence", 1)
            if not existing:
                graph["next_sequence"] = sequence + 1
            previous = graph["nodes"].get(retry_of) if retry_of else None
            node = {
                "id": node_id,
                "type": node_type,
                "label": label,
                "status": "running",
                "parent_id": parent_id,
                "dependency_ids": dependencies,
                "plan_id": plan_id,
                "execution_id": execution_id or (node_id if node_type == "execution" else None),
                "logical_node_id": logical_node_id,
                "actor_id": actor_id,
                "actor_label": actor_label,
                "workstream_id": workstream_id,
                "sequence": sequence,
                "attempt": (previous.get("attempt", 1) + 1) if previous else 1,
                "retry_of": retry_of if previous else None,
                "replan_of": replan_of if replan_of in graph["nodes"] else None,
                # Presentation grouping only; it has no dependency semantics.
                "batch_id": batch_id if batch_id is not None else (existing or {}).get("batch_id"),
                "start_time": existing["start_time"] if existing else _now(),
                "end_time": None,
                "summary": None,
                "artifacts": [],
                "input": None,
                "tool_calls": [],
                "state_delta": {},
                "conversation": [],
                "revision": (existing or {}).get("revision", 0),
            }
            graph["nodes"][node_id] = node
            for source_id in dependencies:
                self._add_edge(graph, source_id, node_id, "control", "declared_predecessor")
            if previous:
                self._add_edge(graph, retry_of, node_id, "retry", "same_logical_step_attempt")
            if node["replan_of"]:
                self._add_edge(graph, replan_of, node_id, "replan", "explicit_replanning")
            node["revision"] = node.get("revision", 0) + 1
            self._write(graph)

    def find_step_node(self, node_id: str, plan_id: Optional[str] = None, parent_id: Optional[str] = None) -> Optional[str]:
        """Return the latest logged graph node for an execution-DAG node.

        A resumed execution phase can have a new ``execution_*`` container,
        while its predecessor was completed in an earlier one.  Looking up the
        logged step avoids fabricating an edge to that new container's ID.
        """
        with self._lock:
            history = self._read()
            matches = [
                node for node in history["nodes"].values()
                if node.get("type") == "step"
                and (node.get("logical_node_id") or (node.get("input") or {}).get("node_id")) == node_id
                and (node.get("plan_id") == plan_id if plan_id else bool(parent_id) and node.get("parent_id") == parent_id)
                and (parent_id is None or node.get("parent_id") == parent_id)
                and (parent_id is not None or history["nodes"].get(node.get("parent_id"), {}).get("type") == "execution")
            ]
        if not matches:
            return None
        return max(matches, key=lambda node: (node.get("sequence", 0), node.get("start_time") or "")).get("id")

    def node(self, node_id: str) -> Optional[dict]:
        with self._lock:
            return self._read()["nodes"].get(node_id)

    @staticmethod
    def _add_edge(graph, source, target, kind, evidence, artifact_ref=None):
        if source == target or source not in graph["nodes"] or target not in graph["nodes"]:
            return
        identity = json.dumps([source, target, kind, artifact_ref, evidence])
        edge = {"id": uuid.uuid5(uuid.NAMESPACE_URL, identity).hex, "from": source, "to": target,
                "kind": kind, "evidence": evidence}
        if artifact_ref:
            edge["artifact_ref"] = artifact_ref
        if not any(value.get("id") == edge["id"] for value in graph["edges"]):
            graph["edges"].append(edge)

    def log_data_delivery(self, source: str, target: str, evidence="result_delivered", artifact_ref=None):
        with self._lock:
            graph = self._read()
            self._add_edge(graph, source, target, "data", evidence, artifact_ref)
            self._write(graph)

    def log_task_fork(self, source: str, target: str):
        """Record an actual task launch from a planner/parent activity.

        A fork is delegation, not a control/data prerequisite or evidence that
        any project change was accepted. Completion never implies a merge.
        """
        with self._lock:
            graph = self._read()
            self._add_edge(graph, source, target, "fork", "task_launched")
            self._write(graph)

    def log_artifact(self, node_id: str, reference: str, *, produced=False):
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            field = "produced_artifacts" if produced else "accessed_artifacts"
            if reference not in node.setdefault(field, []):
                node[field].append(reference)
            if produced:
                # An immutable version has one observed producer. Never assign
                # production merely because a step lists an existing artifact.
                graph.setdefault("artifact_producers", {}).setdefault(reference, node_id)
            else:
                source = graph.get("artifact_producers", {}).get(reference)
                if source:
                    self._add_edge(graph, source, node_id, "data", "artifact_inspected", reference)
            node["revision"] = node.get("revision", 0) + 1
            self._write(graph)

    def log_result(self, node_id: str, result: dict):
        with self._lock:
            graph = self._read()
            if node_id in graph["nodes"]:
                if graph["nodes"][node_id].get("result") == result:
                    return
                graph["nodes"][node_id]["result"] = result
                self._write(graph)

    def log_plan(self, plan: dict):
        if not plan.get("graph_id"):
            return
        with self._lock:
            graph = self._read()
            graph.setdefault("plans", {})[plan["graph_id"]] = plan
            self._write(graph)

    def log_snapshot_context(self, phase: str, goal: str) -> bool:
        """Version visible phase/goal changes in the same durable history clock.

        A final worker phase may change without any new node event. Consumers
        reject stale revisions, so phase and goal are part of the publication
        identity as well as topology. Repeating an unchanged snapshot is a no-op.
        """
        with self._lock:
            if not self._path.exists():
                return False
            graph = self._read()
            context = {"phase": phase, "goal": goal}
            if graph.get("snapshot_context") == context:
                return False
            graph["snapshot_context"] = context
            self._write(graph)
            return True

    def log_node_complete(
        self,
        node_id: str,
        status: NodeStatus,
        summary: Optional[str] = None,
        artifacts: Optional[List[str]] = None,
    ) -> None:
        """Update an existing node with completion info."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            # A cancellation is terminal for this run. A late completion from
            # an already-cancelled async task must not turn the graph green.
            if node.get("status") == "cancelled" and status != "cancelled":
                return
            if (
                node.get("end_time") and node.get("status") == status
                and (summary is None or node.get("summary") == summary)
                and (artifacts is None or node.get("artifacts") == artifacts)
            ):
                return
            node["status"] = status
            node["end_time"] = _now()
            if summary is not None:
                node["summary"] = summary
            if artifacts is not None:
                node["artifacts"] = artifacts
            node["revision"] = node.get("revision", 0) + 1
            self._write(graph)

    def count_nodes_of_type(self, node_type: NodeType) -> int:
        """Return how many nodes of the given type already exist (for loop indices)."""
        graph = self._read()
        return sum(1 for n in graph["nodes"].values() if n["type"] == node_type)

    def nodes_of_type(self, node_type: NodeType) -> list[dict]:
        """Return graph nodes of a type for lifecycle-aware node reuse."""
        graph = self._read()
        return [node for node in graph["nodes"].values() if node.get("type") == node_type]

    def log_node_input(self, node_id: str, input_data: dict) -> None:
        """Store the structured input that was passed to the sub-agent."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            if node.get("input") == input_data:
                return
            node["revision"] = node.get("revision", 0) + 1
            node["input"] = input_data
            self._write(graph)

    def log_tool_call(self, node_id: str, entry: dict) -> None:
        """Append one tool-call record to the node's tool_calls list."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            node["revision"] = node.get("revision", 0) + 1
            node.setdefault("tool_calls", []).append(entry)
            self._write(graph)

    def log_state_delta(self, node_id: str, delta: dict) -> None:
        """Merge delta into the node's accumulated state_delta."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            current = node.get("state_delta") or {}
            if all(key in current and current[key] == value for key, value in delta.items()):
                return
            node["revision"] = node.get("revision", 0) + 1
            node.setdefault("state_delta", {}).update(delta)
            self._write(graph)

    def log_conversation_event(self, node_id: str, entry: dict) -> None:
        """Append one conversation turn, coalescing adjacent streamed text.

        LLM providers may send either a token delta or the entire response so
        far.  Keeping every chunk made the graph noisy and meant the frontend
        could not present a single live response for a running node.
        """
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            conversation = node.setdefault("conversation", [])
            previous = conversation[-1] if conversation else None
            if (
                previous
                and entry.get("type") in {"text", "thought"}
                and previous.get("type") == entry.get("type")
                and previous.get("author") == entry.get("author")
                and isinstance(previous.get("content"), str)
                and isinstance(entry.get("content"), str)
            ):
                content = _merge_streamed_text(previous["content"], entry["content"])
                if content == previous["content"]:
                    return
                previous["content"] = content
                previous["timestamp"] = entry.get("timestamp", previous.get("timestamp"))
            else:
                conversation.append(entry)
            node["revision"] = node.get("revision", 0) + 1
            self._write(graph)

    def mark_running_nodes_cancelled(
        self,
        node_types: Optional[list[NodeType]] = None,
        summary: str = "Cancelled by user",
    ) -> None:
        """Pre-emptively mark running nodes as cancelled.

        Called eagerly on session cancel so the graph reflects cancellation
        before the step executor polls its flag.
        """
        with self._lock:
            graph = self._read()
            now = _now()
            changed = False
            for node in graph["nodes"].values():
                if node.get("status") != "running":
                    continue
                if node_types is not None and node.get("type") not in node_types:
                    continue
                node["status"] = "cancelled"
                node["end_time"] = now
                node["summary"] = summary
                node["revision"] = node.get("revision", 0) + 1
                changed = True
            if changed:
                self._write(graph)

    def mark_running_nodes_failed(self, summary="Worker stopped before completion"):
        with self._lock:
            graph = self._read()
            changed = False
            for node in graph["nodes"].values():
                if node.get("status") == "running":
                    node.update(status="failed", summary=summary, end_time=_now())
                    node["revision"] = node.get("revision", 0) + 1
                    changed = True
            if changed:
                self._write(graph)

    def cancel_step_node_by_number(self, step_number: int, summary: str = "Cancelled by user") -> bool:
        """Find the running step node with input.step_number==step_number and mark it cancelled.

        Returns True if a node was found and updated.
        """
        with self._lock:
            graph = self._read()
            now = _now()
            for node in graph["nodes"].values():
                if (
                    node.get("type") == "step"
                    and node.get("status") == "running"
                    and (node.get("input") or {}).get("step_number") == step_number
                ):
                    node["status"] = "cancelled"
                    node["end_time"] = now
                    node["summary"] = summary
                    node["revision"] = node.get("revision", 0) + 1
                    self._write(graph)
                    return True
        return False

    def cancel_step_node_by_id(self, node_id: str, summary: str = "Cancelled by user") -> bool:
        """Find the running step node with input.node_id==node_id and mark it cancelled.

        Used for DAG-mode cancellation where nodes are identified by string node_id.
        Returns True if a matching node was found and updated.
        """
        with self._lock:
            graph = self._read()
            now = _now()
            for node in graph["nodes"].values():
                if (
                    node.get("type") == "step"
                    and node.get("status") == "running"
                    and (node.get("input") or {}).get("node_id") == node_id
                ):
                    node["status"] = "cancelled"
                    node["end_time"] = now
                    node["summary"] = summary
                    node["revision"] = node.get("revision", 0) + 1
                    self._write(graph)
                    return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> dict:
        if self._path.exists():
            # Do not silently replace damaged history with an empty graph.
            graph = json.loads(self._path.read_text(encoding="utf-8"))
            if graph.get("schema_version", 1) < 2:
                # Legacy edges mixed containment and control. Preserve records,
                # but accept only recorded declared step predecessors as edges.
                graph["legacy_edges"] = graph.get("edges", [])
                graph["edges"] = []
                for node in graph.get("nodes", {}).values():
                    for source in node.get("dependency_ids") or []:
                        self._add_edge(graph, source, node["id"], "control", "legacy_declared_predecessor")
                graph["schema_version"] = 2
            graph.setdefault("next_sequence", max((n.get("sequence", 0) for n in graph["nodes"].values()), default=0) + 1)
            return graph
        return {"schema_version": 2, "session_id": self.session_id, "nodes": {}, "edges": [],
                "next_sequence": 1, "revision": 0, "updated_at": _now()}

    def _write(self, graph: dict) -> None:
        graph["updated_at"] = _now()
        graph["revision"] = graph.get("revision", 0) + 1
        temporary = self._path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(graph, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(6):
                try:
                    os.replace(temporary, self._path)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise
                    # Windows readers may briefly hold a non-delete-sharing
                    # handle. Retry replacement, never fall back to truncation.
                    time.sleep(0.005 * (2 ** attempt))
        finally:
            temporary.unlink(missing_ok=True)


def _merge_streamed_text(current: str, incoming: str) -> str:
    """Merge a streamed delta or cumulative snapshot without duplicated text."""
    if not incoming or current.endswith(incoming):
        return current
    if incoming.startswith(current):
        return incoming
    for overlap in range(min(len(current), len(incoming)), 0, -1):
        if current.endswith(incoming[:overlap]):
            return current + incoming[overlap:]
    return current + incoming
