"""Explicit delivered inputs. Free-form context is never treated as lineage."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, model_validator


class ConsumedInput(BaseModel):
    step_id: Optional[str] = None
    node_id: Optional[str] = None
    artifact_ref: Optional[str] = None

    @model_validator(mode="after")
    def one_reference(self):
        if sum(bool(value) for value in (self.step_id, self.node_id, self.artifact_ref)) != 1:
            raise ValueError("Each consumed input must name exactly one step_id, node_id or artifact_ref")
        if self.artifact_ref:
            pieces = self.artifact_ref.split(":")
            if len(pieces) != 4 or pieces[:2] != ["oaw", "artifact"] or not all(pieces[2:]):
                raise ValueError("Use a versioned oaw:artifact:<collection>:<version> reference")
        return self


def validate_input_references(inputs, history, plan=None, target=None):
    parsed = [ConsumedInput.model_validate(value) for value in inputs]
    for item in parsed:
        if item.step_id:
            source = history.node(item.step_id)
            if source is None or source.get("type") != "step" or source.get("status") != "success":
                raise ValueError(f"Input step {item.step_id!r} must be an existing successful attempt")
        if item.node_id:
            nodes = (plan or {}).get("nodes", {})
            if item.node_id not in nodes or item.node_id == target:
                raise ValueError(f"Unknown or self-referencing plan input {item.node_id!r}")
            # A planned result can only be delivered after the source completes.
            # Require an explicit scheduling edge; never silently add one.
            incoming = {target}
            while True:
                expanded = incoming | {edge[0] for edge in plan.get("edges", []) if edge[1] in incoming}
                if expanded == incoming:
                    break
                incoming = expanded
            if item.node_id not in incoming:
                raise ValueError("A same-plan input must also have an explicit predecessor path")
    return [item.model_dump(exclude_none=True) for item in parsed]
