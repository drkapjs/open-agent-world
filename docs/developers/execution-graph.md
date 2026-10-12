# Execution graph sources

Plugin API **1.30** separates execution producers from the built-in **Agent graph**
card (`core.execution-graph`). Connect **graph → source**, choosing **Observe
execution** (`core.execution-source`). The viewer can switch between connected
sources. Reading never grants tools, starts tasks, stops Runs or changes source state.

## Existing runtime sources

Every `core.agent` card automatically supplies its active Run (or latest Run when idle) and descendant
Runs for the current conversation session. Callable libraries declared with
`NodeSummoningDefinition` supply their summoned attempts, including follow-up
turns. Private summon context IDs are resolved through Run ancestry to avoid
mixing conversations. Agent Barracks and its equipped Summoning card support this
contract. Disconnecting the Summoning card from a library removes that library's
records from its projection immediately.

Agent runtimes can publish richer internal progress through their existing
`AgentCapabilityProvider`, while executing their current invocation:

```python
await provider.publish_execution(config.agent_id, context.run_id, {
    "title": "Build and inspect a structure",
    "phase": "running",
    "nodes": [
        {"id": "coordinator", "kind": "agent", "label": "Coordinator", "status": "running"},
        {"id": "worker", "kind": "agent", "label": "Structure worker", "status": "running",
         "parent_id": "coordinator"},
        {"id": "build", "kind": "plan", "label": "Build", "status": "succeeded"},
        {"id": "inspect", "kind": "plan", "label": "Inspect", "status": "pending",
         "dependency_ids": ["build"]},
    ],
})
```

The host validates ownership of the current invocation and persists the latest
projection with its Run. No viewer needs to exist. The host's terminal Run state
overrides stale active statuses after failure, cancellation or recovery. Saved
Legion templates contain connections and presentation, never Run history.
MatCreator converts its upstream graph into this format at its runtime boundary.
Publishing emits `execution_updated` with the Run and conversation/session IDs,
without graph contents. Viewers immediately reread the authorized projection.
A newer rejected send cannot replace a still-active Run. When a new Run has not
published a detailed projection, the host retains the latest detailed graph from
the same source, session and runtime provider. `run_id` and `phase` identify the
current Run; `previous_run_id` identifies the retained graph. Its node statuses
remain those of the previous execution, and the viewer labels this waiting state.
The first current projection replaces it, including an explicitly empty graph.

## Custom card sources

Cards that own another execution model can provide a read-only adapter:

```python
from open_agent_world.plugin_api import NodeExecutionSource, ExecutionSnapshot

def read_execution(context):
    # context.state is bound to this card's declared shared/session namespace.
    value = context.state.get()["value"].get("execution", {})
    return ExecutionSnapshot.model_validate(value).model_dump(mode="json")

# Add to NodeTypeDefinition:
execution_source = NodeExecutionSource(read=read_execution)
```

Registration automatically adds `core.execution-source`. A custom view declares
`core.execution-viewer` and uses `host.executionSources.list()`, `.read(sourceId)`
and `.subscribe(listener)` from the frontend SDK. Subscriptions report
`connections` when the viewer's connections change
and `execution` for progress, Run transitions or source state changes. Keep the
current view during progress refreshes; discard it on session/source changes or
connection revocation. Polling can recover missed notifications. The host supplies
the active session and rechecks the live connection on every read. Reads hold the same
mutation lock and cancellation lifetime as resource actions; callbacks must bound
their work and must not mutate data or return credentials.

`ExecutionSnapshot` has `title`, `phase`, optional `run_id`, `nodes` and `truncated`.
Each `ExecutionNode` has a unique ID, label, kind (`agent`, `plan` or `tool`), status,
optional `parent_id`, `dependency_ids`, detail and result. Use stable IDs across
progress updates, separate IDs for separate attempts, and an explicit truncated
flag for partial snapshots. Limits are 500 nodes and 2 MiB per projection.

The graph uses layered positions, solid parent links and dashed dependency links.
These relations remain distinct in inspection. Missing endpoints are ignored;
partial or cyclic snapshots cannot create a layout loop. Node colors follow OAW
theme tokens. Status reports describe execution, not scientific acceptance.
The viewer initially fits the graph and follows layout changes until the user
pans or zooms. Drag to pan, scroll to zoom around the pointer, or use the zoom/Fit
buttons. With the canvas focused, arrow keys pan, +/- zoom and Home/0 fit. Progress
updates preserve selection and manual camera position. Task goals are collapsed
by default and can be expanded above the canvas.

This first version is available in the local builder and its Workspace mode.
Published application surfaces do not expose these builder-only endpoints.
