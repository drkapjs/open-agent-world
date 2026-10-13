"""Read-only recovery of references retained by older native checkpoints.

No remote service, provider, scheduler or database is created here. New external
work uses OAW Sandbox operations. A saved job reference is never auto-resubmitted.
"""
import json
import sqlite3
from pathlib import Path

ACTIVE_REMOTE_JOB_STATUSES = frozenset({
    "created", "submitting", "queued", "running", "pause_requested", "paused",
    "resume_requested", "resuming", "succeeded", "collecting", "terminate_requested",
})


def read_job(path: Path, job_id: str):
    if not path.is_file():
        return None
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    value = dict(row)
    if isinstance(value.get("snapshot"), str):
        value["snapshot"] = json.loads(value["snapshot"])
    return value
