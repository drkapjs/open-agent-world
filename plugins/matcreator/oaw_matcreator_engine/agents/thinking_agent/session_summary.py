"""Session-level summary tool for the thinking agent.

Called after execution results return to the planner. Captures the global
narrative (why, key decisions, failures) that per-step trajectory entries miss.
Saved as private session metadata. Reviewed knowledge is saved separately
through connected OAW tools. Writes complete before the worker can exit.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel, Field, ValidationError

from ...workspace import get_workspace_root

logger = logging.getLogger(__name__)


class SessionSummary(BaseModel):
    goal: str = Field(..., description="Original user goal in their own words")
    approach: str = Field(..., description="Overall approach taken and why it was chosen")
    key_decisions: List[str] = Field(
        default_factory=list,
        description="Important choices made during the session (e.g. code choice, parameter values)",
    )
    lessons_learned: List[str] = Field(
        default_factory=list,
        description="Heuristics or warnings useful for future similar tasks",
    )
    failed_attempts: List[str] = Field(
        default_factory=list,
        description="Things that were tried and did not work, with brief reason",
    )
    outcome: str = Field(..., description="One-sentence statement of what was accomplished")


def write_session_summary(summary: dict, tool_context: ToolContext) -> dict:
    """Write a session-level summary capturing the global narrative of this session.

    Call this once per session, after execution has completed and results are
    available. Unlike per-step summaries, this captures WHY decisions were made,
    what failed, and high-level lessons that apply to future similar tasks.

    The summary is saved alongside the private trajectory JSONL. Use OAW
    knowledge tools to explicitly publish reviewed findings.

    Args:
        summary: Dict with keys:
            - goal (str): Original user goal in their own words.
            - approach (str): Overall approach and reasoning.
            - key_decisions (list[str]): Important choices made.
            - lessons_learned (list[str]): Heuristics for future tasks.
            - failed_attempts (list[str]): What was tried and failed.
            - outcome (str): One-sentence statement of what was accomplished.
    """
    try:
        validated = SessionSummary(**summary)
        data = validated.model_dump()

        session_id = tool_context._invocation_context.session.id
        traj_dir = get_workspace_root() / "trajectories"
        traj_dir.mkdir(parents=True, exist_ok=True)
        summary_path = traj_dir / f"{session_id}_summary.json"

        data["session_id"] = session_id
        data["timestamp"] = datetime.now(timezone.utc).isoformat()

        _write_summary_file(summary_path, data)

        return {
            "status": "ok",
            "path": str(summary_path),
            "message": "Session summary saved. Use OAW knowledge tools to explicitly save reviewed findings.",
        }
    except ValidationError as exc:
        return {
            "status": "error",
            "errors": exc.errors(),
            "message": "Session summary validation failed. Fix the errors and retry.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Unexpected error: {exc}",
        }


def _write_summary_file(path: Path, data: dict) -> None:
    """Write bounded session metadata before worker shutdown."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
    logger.info("Session summary written to %s", path)
