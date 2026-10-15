"""Maintained OAW adaptations layered over, and attributed separately from, upstream snapshots."""
from copy import deepcopy
import hashlib

REVISION = 2
RUNTIME_NOTES = """# OAW runtime compatibility (reviewed 2026-10-14)

These notes adapt the imported examples; the scientific procedures are not
certified for every model, calculator or CLI version. Read installed command
help and inspect actual model metadata before executing an example.

## Bohrium CLI

Check `node --version`, `npm --version` and an existing `bohr version` first.
If installation is required, use `npm install -g @dptech-corp/bohr-cli` in the
persistent Sandbox HOME prefix. Verify the installed executable with
`bohr version` and `bohr doctor --offline`. Record its version in the task report.
The imported curl-to-shell download endpoint is obsolete for this adaptation.

CLI flags are version-specific. Read `bohr project list --help` before querying.
Current agent-oriented CLI examples use `bohr project list -o json`; old documents
may use `--json`. Installed help takes precedence when the two disagree.
Select BOHRIUM_PROJECT_ID explicitly. Never choose the first accessible project.
Filter job/group queries to that project using flags shown by installed help;
omit unrelated project data and billing details from reports.

Sources:
- https://bohrium-doc.dp.tech/docs/bohrctl/install/
- https://www.bohrium.com/bohr-cli/intro

## Environment configuration

Inspect configured resources and their errors without printing credential values.
`configured` means a binding exists; `available` validates its host path, not a
successful access from the Sandbox. Use the mapped runtime path and test access.
Resolve image and machine choices before rendering/submitting a job template:

| Workflow | Image variable | Machine variable |
| --- | --- | --- |
| MatterSim / MatterGen | BOHRIUM_MAT_IMAGE | BOHRIUM_MAT_MACHINE |
| LAMMPS / DeepMD | BOHRIUM_DEEPMD_IMAGE | BOHRIUM_DEEPMD_MACHINE |
| DPA | BOHRIUM_DPA_IMAGE | BOHRIUM_DPA_MACHINE |
| VASP | BOHRIUM_VASP_IMAGE | BOHRIUM_VASP_MACHINE (optional override) |

For any missing workflow-specific choice, use BOHRIUM_IMAGE / BOHRIUM_MACHINE
if explicitly configured, otherwise ask for the missing scientific/resource
choice. These are resolution conventions, not automatic host variable aliases:
substitute the resolved values into the job configuration explicitly. Keep
existing variable names working; do not rename saved user configuration.

Do not assume a model must be named frozen.pth or that every model uses the same
freeze/conversion command. Inspect its format, DeepMD/LAMMPS versions and required
head, then run a bounded compatibility probe before the full calculation.

## Persistent work and remote jobs

Each Sandbox command has a fresh process and /tmp. Store cross-command files in
the assigned research/<board>/<task>/<attempt> directory or HOME. These are
separate output directories in a shared workspace, not filesystem isolation.
Only the coordinator manages the shared Sandbox lifecycle.

Discover host tools with oaw_list_tools and invoke their returned capability IDs.
For native MatCreator, use inspect_background_jobs to discover provider schemas,
then submit_background_job with provider=matcreator.bohrium-batch and a stable
request_key. Stage files in the authorized Sandbox first. Keep the host job ID
and provider external_id distinct. Use attach_background_job for existing jobs.
The host persists a submission reservation before calling the provider; unknown
means reconciliation is required, never permission to submit a replacement.

Call submit_step_result(status="waiting", job_ids=[host_job_id], ...) to hand off
monitoring and end the worker. The host polls without model calls and resumes the
same approved plan after a terminal observation. Backend shutdown pauses checking;
external compute continues and checking resumes after restart. Local Sandbox
processes do not survive restart. Stop/pause monitoring does not cancel cloud work.
Use control_background_job(action="cancel") only when external cancellation is
intended, and check its observed state. Download outputs to the Sandbox through
the installed CLI, then publish files as artifacts; never inline binary outputs.

Task-board report_delegated_task/defer and upstream remote-job command names are
not native MatCreator tools. Only use tools present in the live OAW catalog.
Missing submit_step_result is a missing result, not proof of a timeout or absence
of cloud jobs. Scientific acceptance still requires inspecting real outputs.

Tavily-dependent retrieval requires its configured API credential. Check the
skill's credential requirement before starting; if unavailable, report that
specific capability and use another already-authorized retrieval method when
appropriate. A missing retrieval key does not disable all Sandbox networking.
"""


def adapt_package(source):
    package = deepcopy(source)
    changed = False
    for skill in package["skills"]:
        family = skill["id"].split("--", 1)[0]
        if family not in {"bohrium", "remote-job", "lammps", "deepmd", "mattersim", "mattergen", "vasp-pymatgen", "tavily"}:
            continue
        defaults = skill.setdefault("defaults", {})
        if defaults.get("compatibility", {}).get("oaw_revision") == REVISION:
            continue
        changed = True
        # Upstream hashes retain their original meaning. Adapted content has its
        # own hashes and revision rather than pretending to be unchanged upstream.
        def update(text):
            return (text.replace("curl -fsSL https://bohrium.com/download/bohr | sh", "npm install -g @dptech-corp/bohr-cli")
                .replace("bohr project list --json", "bohr project list -o json")
                .replace("User specified `BOHRIUM_PROJECT_ID` > The first project ID in `bohr project list -o json`",
                         "Use the explicitly selected `BOHRIUM_PROJECT_ID`; never default to the first accessible project"))
        prefix = ("OAW compatibility: read references/oaw-runtime.md before remote execution, CLI installation, "
            "model conversion or credential-dependent retrieval. Installed tool help takes precedence over legacy examples.\n\n"
        )
        skill["instructions"] = prefix + update(skill["instructions"].removeprefix(prefix))
        files = skill.setdefault("files", {})
        for name, content in list(files.items()):
            if isinstance(content, str) and name.endswith(".md"):
                files[name] = update(content)
        files["references/oaw-runtime.md"] = RUNTIME_NOTES
        defaults["compatibility"] = {"oaw_revision": REVISION, "reviewed_at": "2026-10-14",
            "verification_scope": "CLI documentation and OAW contracts; scientific execution remains unverified",
            "instruction_sha256": hashlib.sha256(skill["instructions"].encode()).hexdigest(),
            "runtime_notes_sha256": hashlib.sha256(RUNTIME_NOTES.encode()).hexdigest()}
    if changed:
        package["version"] = "0.1.2"
    return package
