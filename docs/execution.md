# Plugin work-source execution (API 1.3)

[Documentation](README.md)

OAW is a plugin + Agent platform. Task boards, approval queues, staged research,
and domain workflows should share execution infrastructure without sharing one
business model. The Task Board plugin is one adapter for this infrastructure.

## Responsibilities

| Layer | Owns |
| --- | --- |
| Host Run manager | Runtime provider selection, Agent capacity, Run lifecycle, cancellation, durable output |
| Host node execution | Admission, live connection checks, one batch per source, bounded scheduling, attempts, stop and recovery |
| Plugin | Work identities, readiness, prompts, executor selection, result interpretation and acceptance |
| Legion | Membership, shared settings/state and portable composition; not a second scheduler |
| Frontend | Common controls/history and optional specialized document editors |

A plugin's `NodeTypeDefinition.execution` accepts a public
`NodeExecutionDefinition`. It requires a `document` and three pure callbacks:

```python
from open_agent_world.plugin_api import (
    ExecutionPolicy, NodeExecutionDefinition, WorkItem,
)

def items(value):
    # This is a review queue, with no DAG or Task Board schema.
    return [WorkItem(
        id=entry["id"], prompt=entry["request"], agent_id=value["reviewer"],
        ready=entry["stage"] == "queued",
        retryable=entry["stage"] == "failed",
    ) for entry in value["entries"]]

def apply_outcome(value, outcome):
    # Return a new JSON document. The model validates it before persistence.
    stage = {"running": "working", "succeeded": "awaiting_acceptance"}.get(
        outcome.status, "failed")
    return {**value, "entries": [
        {**entry, "stage": stage, "run_id": outcome.run_id,
         "result": outcome.text, "error": outcome.error}
        if entry["id"] == outcome.item_id else entry
        for entry in value["entries"]
    ]}

execution = NodeExecutionDefinition(
    items=items,
    apply_outcome=apply_outcome,
    policy=lambda value: ExecutionPolicy(max_parallel=1, pause_on_failure=True),
    executor_relationship="example.review.executor",
    control_capability_kind="example.review.control",  # optional
)
```

Register the named relationship in the same plugin with the work-source node as
source and a `core.agent` target. This is dispatch authority, independent from
any Agent-to-document access edges. If using Agent controllers, register the
control capability handler and grant it separately. Its handler calls
`context.node_execution_action(capability, action, arguments)` with `read`, `start`
or `stop`. Plugins do not receive the database, service container, or RunManager.

`WorkOutcome` carries item ID, Run ID (possibly absent after interrupted admission),
status, text and error. Callbacks must be deterministic and idempotent for the same
outcome: recovery may redeliver an outcome after persistence was interrupted.
Persist a processed Run ID in the document when acceptance includes accumulating
or incrementing values. Run success does not force plugin acceptance. The example
enters `awaiting_acceptance`; the bundled Task Board instead enters `done`.

## Host APIs and execution behavior

```text
GET  /api/nodes/{id}/execution
POST /api/nodes/{id}/execution/start {expected_revision, item_id?}
POST /api/nodes/{id}/execution/stop
```

The snapshot contains batch status, active flag, authorized executor choices,
work readiness metadata (without prompts), and recent attempts. Start requires
the current document revision. Omit `item_id` to repeatedly dispatch ready work;
provide it to execute/retry only that item. A batch dispatches each ID at most once.
The plugin may expose newly generated work after processing outcomes.

The host allows 1–8 concurrent Runs per source and serializes items assigned to
the same Agent. Agent admission limits still apply across sources. At most 1000
items may be returned and 1000 attempts dispatched per batch. Starting another
batch retains the previous 200 attempts; the current batch's attempts remain
visible, and durable Run records are kept separately. The built-in history UI
shows the latest 30 entries.

The host records `caller_kind="work"`, a unique batch caller ID, and a namespaced
`task_id=<source_node_id>:<item_id>` on each Run. Before awaiting Run admission it
persists an attempt reservation. Restart can therefore recover a Run even if the
process stopped before linking its ID to the attempt. Terminal outcomes that were
not applied to the document are reconciled. Remaining incomplete work is marked
interrupted; nothing automatically restarts.

Execution ownership is explicit: a controller starts a detached batch and can
query/stop it through its granted capability. Its own Run ending does not cancel
the batch. Permission revocation prevents new dispatch; a dispatch error stops
the batch and settles its in-flight Runs. Normal work failure with
`pause_on_failure=True` lets in-flight peers finish but admits no new work.

For this first version, active execution locks document edits and source deletion.
Stop before changing the plan. Agent unavailability is surfaced as a batch error,
not silently retried. Retry and restart require an explicit request. External job
polling, persisted timers, automatic retry/backoff, live replanning, and distributed
workers are intentionally not implemented by this local execution service.

## Reuse and user experience

Simple use needs only an executor, concurrency choice, and failure behavior. A
plugin without execution remains fully usable as a document resource. The host
catalog exposes `has_execution`; executable plugins without a specialized view
receive a generic workspace with run/stop/retry and history controls. Specialized
views can reuse `useNodeExecution` and `NodeExecutionControls`.

Legion templates capture plugin documents via `capture` and remap internal node
references via `remap_references(value, ids)`. Template capture uses live IDs to
template keys; instantiation uses template keys to new IDs. External references
must be cleared. Execution history and Run IDs should not be copied into presets.
No material-science-specific state, tool name or dependency rule is built into
the host contract.

## Long computations and durable waiting (API 1.33)

A Run is a bounded Agent turn. A background job is separately persisted external
work that can outlive that turn and backend restarts. Waiting for a job ends the
turn and releases the runtime worker; it does not extend the Run deadline. The
existing host continuation loop observes jobs without calling a model. Only a
terminal observation or an uncertain submission admits a new Agent turn, through
the same capacity, permission, queued-input and continuation-budget checks used
by work sources. The 64-turn limit counts Agent continuations, not status probes.

A conversation turn that registers `wait_background_jobs` ends with a durable
system notice explaining automatic monitoring and continuation. Earlier progress
messages remain in history without becoming a final Agent reply. Startup restores
missing notices, including for older turns that incorrectly promoted progress to
their final reply. An explicit empty provider completion clears the Run output;
only completions that omit output retain the legacy latest-message fallback.

Agents with an authorized Sandbox discover these tools in their live catalog:

| Tool | Contract |
| --- | --- |
| `inspect_background_jobs` | Read records scoped to this Agent/session and discover provider specification schemas |
| `submit_background_job` | Persist a reservation before submission; reuse the same `request_key` to recover its record |
| `attach_background_job` | Verify existing external work without submitting; supply `job_id` to reconcile an uncertain reservation |
| `wait_background_jobs` | Persist continuation intent and then end the Agent turn |
| `control_background_job` | Pause/resume monitoring, acknowledge an outcome, request external cancellation, or explicitly detach tracking |

Host IDs and provider `external_id` values are distinct. Reusing a request key
with different parameters is rejected. The journal stores ownership, identity,
state, request fingerprint and notification admission, not submission scripts or
credentials. Credentials remain in the authorized Sandbox. An interrupted submit
becomes `unknown`; the host never automatically resubmits it. This prevents replay
through the same key, not duplicates created using a different key or outside OAW.

Transient inspection failures retain the prior job state and back off up to one
hour. Provider calls are bounded: preflight/inspection/cancellation at 90 seconds,
submission at 180 seconds. Compute duration is a separate explicit provider budget.
Permission is checked again after asynchronous calls and before continuation.
Revocation pauses monitoring; elapsed time never proves a job succeeded or vanished.

User Stop pauses automation and does **not** cancel remote compute. Normal host
shutdown preserves pending external monitoring. A continuation already admitted
before a crash is never blindly replayed; its interrupted record requires explicit
inspection/resume. Cancellation is confirmed only by observing the provider's
terminal state. Resource/session deletion and provider disable are blocked while
work is tracked. `detach` explicitly relinquishes tracking and allows deletion;
it does not stop the external job or its charges.

The backend must be running to check or deliver notifications. Closing it pauses
local observation; remote compute can continue and checking resumes at startup.
Startup schedules an immediate check for active jobs instead of retaining a
previous process's retry delay. Providers using Sandbox commands restore a
stopped Sandbox only through the owning Agent's current `sandbox.start` grant;
execute-only access still requires the Sandbox to be running. Paused monitors
and stopped Agent turns remain paused. An unavailable Sandbox is reported in
the job's observation error without changing the remote computation state.
Ordinary commands and managed Python installation use the same on-demand
preparation. The Sandbox UI accepts commands without a separate Start action;
Stop remains available for active work, recovery, and editing locked settings.
There is no OS wake service. `core.sandbox-operation` can attach to an already
admitted operation, but local Sandbox processes remain interrupted after host
restart. This release is a single-host monitor, not a multi-worker distributed
scheduler. Scientific success and artifact collection remain the Agent/plugin's
responsibility after provider completion.

Trusted plugins register `BackgroundJobProvider` through
`registration.register_background_job_provider(id, definition)`. Import
`BackgroundJobProvider`, `BackgroundJobContext` and `JobObservation` from
`open_agent_world.plugin_api`. A provider declares a Pydantic specification model,
an asynchronous `inspect(context, external_id)`, and optionally `prepare`,
`submit(context, specification, request_id)` and `cancel` callbacks. `prepare`
must have no submission side effects. Return the stable external ID immediately
from `submit`; never poll the computation inside that callback. `context.execute`
runs bounded argv through the live authorized Sandbox. Providers must not retain
credentials in observations. The MatCreator Pack supplies a Bohrium batch adapter.
