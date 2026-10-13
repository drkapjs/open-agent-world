"""Read-only execution projections shared by runtime providers and graph cards."""
import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.plugins.resources import NodeResourceContext

MAX_EXECUTION_NODES = 10000
MAX_EXECUTION_BYTES = 8 * 1024 * 1024


class ExecutionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=512)
    label: str = Field(max_length=500)
    kind: Literal["agent", "plan", "tool"] = "agent"
    status: str = Field(default="pending", max_length=80)
    parent_id: str | None = None
    # Legacy shorthand for control dependencies only. Parentage and ordering
    # are never dependencies. New providers should publish typed edges.
    dependency_ids: list[str] = Field(default_factory=list, max_length=10000)
    role: str = Field(default="", max_length=80)
    execution_id: str | None = Field(default=None, max_length=512)
    plan_id: str | None = Field(default=None, max_length=512)
    sequence: int | None = Field(default=None, ge=0)
    started_at: str | None = Field(default=None, max_length=80)
    finished_at: str | None = Field(default=None, max_length=80)
    attempt: int | None = Field(default=None, ge=1)
    retry_of: str | None = Field(default=None, max_length=512)
    replan_of: str | None = Field(default=None, max_length=512)
    # Activity ownership is independent of containment and causal dependencies.
    actor_id: str | None = Field(default=None, max_length=512)
    actor_label: str = Field(default="", max_length=500)
    workstream_id: str | None = Field(default=None, max_length=512)
    workstream_label: str = Field(default="", max_length=500)
    operation: str = Field(default="", max_length=80)
    record_type: Literal["activity", "context", "planned"] = "activity"
    realized_by: str | None = Field(default=None, max_length=512)
    artifact_refs: list[str] = Field(default_factory=list, max_length=1000)
    detail: str = Field(default="", max_length=20000)
    result: str = Field(default="", max_length=20000)


class ExecutionEdge(BaseModel):
    """An observed relationship, never an inference from chronology or parentage."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=2048)
    source: str = Field(min_length=1, max_length=512)
    target: str = Field(min_length=1, max_length=512)
    kind: Literal["control", "data", "retry", "replan", "fork", "merge"]
    artifact_ref: str | None = Field(default=None, max_length=2048)
    evidence: str | None = Field(default=None, max_length=2000)


class ExecutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = ""
    # When a new Run has not published its projection, the host may retain the
    # last detailed graph. Its provenance must not be mistaken for current work.
    previous_run_id: str = ""
    title: str = Field(default="", max_length=2000)
    phase: str = Field(default="idle", max_length=80)
    # Pagination is a transport concern. The retention policy controls durable
    # writes; page responses always assemble one complete visible snapshot.
    mode: Literal["full", "page"] = "full"
    history_id: str = Field(default="", max_length=512)
    revision: int = Field(default=0, ge=0)
    # History providers opt into durable union by activity ID. Omission is not
    # deletion; legacy snapshot providers retain replacement semantics.
    retention: Literal["snapshot", "history"] = "snapshot"
    nodes: list[ExecutionNode] = Field(default_factory=list, max_length=MAX_EXECUTION_NODES)
    edges: list[ExecutionEdge] = Field(default_factory=list, max_length=50000)
    total_nodes: int | None = Field(default=None, ge=0)
    next_cursor: str | None = Field(default=None, max_length=2048)
    truncated: bool = False

    @model_validator(mode="before")
    @classmethod
    def legacy_control_edges(cls, value):
        # Omitted edges is the legacy contract. An explicitly supplied array,
        # including [], is authoritative and must not acquire inferred edges.
        if not isinstance(value, dict) or 'edges' in value:
            return value
        nodes = value.get('nodes', [])
        if not isinstance(nodes, (list, tuple)):
            return value  # Leave invalid node collections to field validation.
        edges = {}
        for node in nodes:
            if isinstance(node, ExecutionNode):
                target, sources = node.id, node.dependency_ids
            elif isinstance(node, dict):
                target, sources = node.get('id'), node.get('dependency_ids', [])
            else:
                continue  # Normal node validation reports malformed values.
            if not target or not isinstance(sources, list):
                continue
            for source in sources:
                if not isinstance(source, str):
                    continue
                identity = hashlib.sha256(json.dumps([source, target]).encode()).hexdigest()[:32]
                edges[identity] = {'id': f'legacy-control:{identity}', 'source': source,
                    'target': target, 'kind': 'control', 'evidence': 'declared_dependency_ids'}
        return {**value, 'edges': list(edges.values())}

    @model_validator(mode="after")
    def unique_ids(self):
        if len({node.id for node in self.nodes}) != len(self.nodes):
            raise ValueError("Execution node IDs must be unique within a snapshot")
        if len({edge.id for edge in self.edges}) != len(self.edges):
            raise ValueError("Execution edge IDs must be unique within a snapshot")
        return self


@dataclass(frozen=True, slots=True)
class NodeExecutionSource:
    """Custom sources return ExecutionSnapshot JSON from their own bound state.

    Agent and callable-library sources are supplied by the host automatically.
    Summoning adapters opt in with kind='summoning-adapter'; their live outgoing
    summoning grants define the libraries whose instances may be observed.
    Custom read handlers obey the same lock/cancellation contract as resource
    actions. They must be read-only, bounded, and must not return credentials.
    """
    read: Callable[[NodeResourceContext], dict] | None = None
    kind: Literal["custom", "summoning-adapter"] = "custom"

    def __post_init__(self):
        if self.kind not in {'custom', 'summoning-adapter'}:
            raise ValueError('Unknown execution source kind')
        if (self.kind == 'custom') != callable(self.read):
            raise ValueError('Custom execution sources require read; summoning adapters use the host reader')
