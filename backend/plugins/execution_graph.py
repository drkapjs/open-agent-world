"""Read-only execution projections shared by runtime providers and graph cards."""
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.plugins.resources import NodeResourceContext


class ExecutionNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=512)
    label: str = Field(max_length=500)
    kind: Literal["agent", "plan", "tool"] = "agent"
    status: str = Field(default="pending", max_length=80)
    parent_id: str | None = None
    dependency_ids: list[str] = Field(default_factory=list, max_length=500)
    detail: str = Field(default="", max_length=20000)
    result: str = Field(default="", max_length=20000)


class ExecutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = ""
    # When a new Run has not published its projection, the host may retain the
    # last detailed graph. Its provenance must not be mistaken for current work.
    previous_run_id: str = ""
    title: str = Field(default="", max_length=2000)
    phase: str = Field(default="idle", max_length=80)
    nodes: list[ExecutionNode] = Field(default_factory=list, max_length=500)
    truncated: bool = False

    @model_validator(mode="after")
    def unique_ids(self):
        if len({node.id for node in self.nodes}) != len(self.nodes):
            raise ValueError("Execution node IDs must be unique within a snapshot")
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
