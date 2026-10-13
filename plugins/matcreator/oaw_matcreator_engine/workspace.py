"""Private orchestration storage; scientific files belong to OAW Sandboxes."""
import os
from pathlib import Path

ADK_DIR = Path(os.environ["MATCREATOR_HOME"]).resolve() / ".adk"
WORKSPACE_ROOT = Path(os.environ["MATCLAW_WORKSPACE"]).resolve()


def get_workspace_root() -> Path:
    return WORKSPACE_ROOT


def get_session_workdir(session_id: str, custom_workdir=None) -> Path:
    # Old checkpoints may contain workdir/output_dir; they cannot redirect the
    # Pack's writes outside its host-assigned session directory.
    return WORKSPACE_ROOT


def init_session_workdir(session_id: str, custom_workdir=None) -> Path:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT
