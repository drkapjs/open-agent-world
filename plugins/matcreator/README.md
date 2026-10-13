# MatCreator on OAW

This plugin publishes **MatCreator native research**, a dedicated **MatCreator
Agent**, four portable Toolsets and a Know-Do Graph. Its Legion connects the
host's reusable **Agent graph** card to the native Agent.

## Native MatCreator Agent

Use **MatCreator native research** for the Pack-owned planner, execution
orchestrator and recursive step executors. Their maintained Python source lives
in `oaw_matcreator_engine/` and is included in the Pack wheel alongside the
`oaw_matcreator/` host adapter. Editing that source takes effect in the next
worker; no upstream checkout, MatCreator PyPI/Git installation, source fingerprint
update or separate service is required.

The engine was adapted from AI4MS/MatCreator revision
`0328c8b814499cbf3e49116323cee050145fa08f`. Its import provenance is recorded in
[`oaw_matcreator_engine/UPSTREAM.json`](oaw_matcreator_engine/UPSTREAM.json);
this is historical attribution, not a runtime lock. See
[engine ownership](oaw_matcreator_engine/UPSTREAM.md) for the retained modules
and the OAW adaptations.

Normal OAW setup and development startup prepare third-party orchestration
libraries through the Pack's `open_agent_world.setup` hook. Desktop packaging
uses the same hook. Google ADK, LiteLLM and JSON Repair are declared in
`oaw_matcreator/native-requirements.txt` and installed in an isolated directory
under the destination Python's `oaw-runtimes/matcreator/`. Scientific libraries
remain in connected Sandboxes. These dependencies are not added to OAW core.

Setup reuses verified installations, serializes concurrent calls and retries
partial installs. Hosts older than Python 3.12 receive a managed interpreter.
Workers use `-I -S` and explicitly load the Pack source plus its third-party
libraries. Execution never downloads dependencies. The dependency cache identity
is independent of engine source edits. Previous runtime caches are left intact;
new setup installs only the current third-party requirements.

Existing `matcreator.agent` cards, the `matcreator.native` provider ID, session
storage paths and checkpoint format are retained. Obsolete `python_executable`
and `source_directory` config fields are discarded on validation; external
engine/interpreter override paths are no longer exposed or executed. Existing
saved workspace layouts remain user-owned.

On Windows, `scripts/dev.ps1` prepares and launches the persistent
`backend/.venv/Scripts/python.exe`, matching normal setup. A `uv run
--with-editable` overlay has its own installation prefix and cannot see that
runtime. Missing-runtime errors report the backend interpreter and expected
directory; Agent details expose the managed environment's availability.

Only orchestration dependencies are installed in this environment. Materials
libraries, CLI tools, remote-compute credentials and licensed programs belong to
the connected Sandbox. The native runtime supports ADK/LiteLLM model connections
(OpenAI, Anthropic, Gemini and legacy); the host's TypeSafe-only adapter is not
supported by this ADK execution engine.

In **normal** mode, MatCreator first presents its plan and waits for approval in
Conversation. **flash** mode delegates directly without a DAG. The Legion
connects the Agent to Conversation, Sandbox, Research knowledge, Research
artifacts. The separate **Agent graph** observes the Agent through a read-only
connection. Structure viewer follows files from
Conversation and Sandbox. No OAW Barracks or second task scheduler is deployed.

The Pack owns its orchestration tools, graph validation, recursive runner,
result schemas and recovery ledger. Planner and child agents are constructed
with the same explicit OAW resource bridge; they never construct standalone
Shell/Python, remote-provider or knowledge-store tools:

| Resource | Connected OAW tool/view |
| --- | --- |
| Shell/Python and files | Sandbox inspection, execution, operation waiting and file browser |
| Skills and memory | Know-Do Graph search/inspection, skill resources/scripts, save memory |
| Output files | Publish/inspect immutable OAW artifact versions; Structure viewer |
| Plans and subagents | Host Agent graph through the public execution-source contract |
| Conversation and stop | OAW Conversation and Run controls |

Tools are discovered through `oaw_list_tools` and invoked through
`oaw_invoke_tool`; each call rechecks current graph authorization. Removing an
edge revokes the tool for all internal subagents. Native host shell tools, native
knowledge databases, native remote-job submission tools and configured remote
A2A agents are not exposed. Existing scientific commands may be run through an
authorized Sandbox; remote job monitoring must use those connected tools and
retain operation/job IDs, rather than assuming the upstream web server's job
monitor is running.

Unknown function names return tool feedback in the same model loop. Planning,
execution orchestration and every recursive Step agent share this behavior.
Native function arguments are validated before invocation; execution, result
encoding and tool-local cancellation failures are contained per call. One bad
call does not cancel unrelated calls in the same batch. Failed calls are never
automatically replayed: an operation may already have produced side effects.
Run cancellation still cancels and joins the active tool work.

Each card/conversation pair has its own private worker state and atomic session
checkpoint under the host data root. New turns load that checkpoint in a fresh
worker; stopping a Run cancels/join its OAW tool calls and closes the worker.
Interrupted work never restarts automatically. Pack attempt recovery runs
on the next requested turn. Private runtime paths, credentials, checkpoint data
and execution snapshots are excluded from saved Legion templates. The execution
view is a projection; an unavailable view does not abort scientific work.
On restore, unanswered tool calls receive matching responses that explicitly
report an unknown outcome. Existing results are retained and no operation is
replayed by this repair. Model/transport failures outside tool dispatch and
worker/persistence faults remain Run failures.

Artifacts are verified through the current OAW artifact read capability, using
`oaw:artifact:<collection>:<version>` references. Sandbox paths are never treated
as host files. Native `success` and OAW Run success remain execution claims, not
proof of scientific correctness.

Validation includes host deployment/template/session/revocation checks and a
real Pack-engine + ADK test with a deterministic local model endpoint. Set
`OAW_MATCREATOR_MANAGED=1` after normal plugin setup. It exercises planning,
checkpoint reload, execution, recursive child tools and final graph status.
An additional isolated test rejects imports of external MatCreator, OAW core,
and the old standalone knowledge stack, and checks dependency joins, recovery,
artifact references and revoked access. This is not a real-model or scientific
calculation benchmark.

The source-plugin setup hook and `.oawpack` archive installation are distinct
host mechanisms. This change packages the engine in the wheel and validates
source setup; it does not add a new generic archive runtime installer.

## Workspace layout (revision 2)

- Left: **Agent graph**, a vertical execution history with bounded parallel
  tracks, folded completed history and expandable execution steps. Select a
  node to follow typed dependencies, inspect outputs or focus its local DAG.
- Center: **Conversation**, **Skills & knowledge**, **Structure viewer**,
  **File preview**, **Research artifacts**, and **Research files & compute** tabs.
- Right: **Sessions** above **Files**. Both stay visible while changing center tabs.

**Agent graph** belongs to Core essentials and also works outside MatCreator.
Connect it to ordinary Agents, Agent Barracks, equipped Summoning cards or a
custom execution source, then select the source in its toolbar. History tracks
show recorded time overlap, never dependencies. In the local DAG, solid edges
mean control dependencies, dashed edges mean observed data delivery or artifact
inspection, and dotted edges mean retry/replanning. Parentage remains containment. Runtime producers and other Packs
can use the public [execution source API](../../docs/developers/execution-graph.md).

Clicking a file reveals the detached File preview. A newly opened, valid structure
from Sandbox or Conversation reveals Structure viewer after parsing succeeds;
its temporary attachment preview then closes. Unsupported/invalid files keep the
normal preview. Pinned views, reloads and background refreshes do not navigate.

This uses the public `host.workspace.activate()` API. Activation is local to the
open workspace and does not save or recompose the layout. User-hidden sections,
layout editing and deployed section permissions are respected. Conversation
drafts, file selections and plugin state retain their original React owner.
See [workspace navigation](../../docs/developers/workspace-design.md#activate-content-after-an-operation).

Revision 2 is the default for new deployments. Existing saved layouts remain
user-owned; redeploy the preset or arrange its tabs with **Edit layout**.

## Execution history and dependency evidence

Pack 0.7 requires OAW plugin API 1.31. The Pack records immutable Step attempts,
plan identities, timestamps, retry/replan links and versioned artifact producers.
The host stores a full read-only projection with each Run and exposes authorized,
revision-consistent pages. A completed Conversation turn does not turn unfinished
Steps into successes. New turns retain the previous graph until their first
projection arrives; the stable history ID preserves selection across these turns.

The default view uses at most three chronological tracks. Older completed groups
fold automatically; running, failed and waiting work remains visible. Cross-execution
references name their real sources without drawing long lines through unrelated
history. Clicking one reveals its actual target Step and local dependencies.
Execution expansion, Step graph, additional dependency hops, focus and return all
use the same node identities. Virtualized history rows and a bounded local graph
keep 300?500px panels usable. The local lens shows at most 160 nodes with an
explicit limit notice; continue by selecting a related node.

`consumed_inputs` explicitly names a successful `step_id`, a same-plan predecessor
`node_id`, or a versioned `artifact_ref`. A data edge is recorded when the result
is delivered, not merely when the plan declares it. Successful artifact inspection
records access evidence; it does not prove that the model used the data in its
reasoning. Free-form contextual references are not reconstructed. Sources outside
this private Agent/session history have no fabricated producer node. Older records
lack evidence that was never recorded; migration preserves known identities and
explicit control dependencies without inventing historical provenance.

The host accepts up to 10,000 nodes, 50,000 edges and 8 MiB per projection. The Pack
preserves complete history on disk and declares presentation limits when necessary.
History folding does not aggregate or rewrite real edges. Runtime scheduling,
resource access and scientific execution remain Pack-owned.

## Skill package provenance

MatCreator source: `theAfish/MatCreator`, `devel` commit
`a1a57688cdb7fc476498476cc388f932b6e83d6a`.

The package loader uses OAW SkillPackage directly. Materials Core contains
structure manipulation/conversion, plotting and supporting guides; Atomistic
Simulation contains ASE, DFT/MD, phonons and equations of state; Materials AI
contains ML potentials, training and the complete MatterGen family; Research and
Remote Compute contains database/search and remote-provider skills, including the
complete Tavily family. Nested SKILL.md files, scripts, references, binary assets,
frontmatter and source hashes are retained. OAW's internal Skill resource members
remain inside their Toolset; there is no separate published card type per skill.

## Knowledge and resource lifecycle

Publisher knowledge is immutable. Edit mode can save a user-owned copy, create
new entries and deliberately create/remove typed relationships. Memories remain
pending until reviewed into a Heuristic or Procedure. Promotion records evidence
and `derived_from` relationships; it never marks knowledge scientifically tested
without an explicit editor decision. Agent inspections increment usage metadata.

Assimilation is a pure plugin conversion plus a host transaction. The target
document, recreated Skill resource members and source consumption commit together.
The host refuses non-document sources or sources with external lifecycle effects,
checks both document revisions, and buffers mutation events until commit. Scripts
remain Skill files, not graph-node text. Meaningful procedure references become
Procedure nodes; other support files remain addressable resources.

Palette drops use the same transformation endpoint with `source_type` instead of
`source_id` and `source_revision`. The host accepts only user-creatable inert
document types with declared initial content. Preview is read-only; confirmation
checks the destination revision and writes the graph and its members atomically.
Palette provenance records the type and plugin, with no source node ID.

Each imported package has an immutable SHA-256-keyed source snapshot, including
complete original adapted package bytes and source provenance. Download snapshots
from the Window. A snapshot's `package` value is an ordinary `SkillPackage` and can
be exported using `open_agent_world.skill_packages.export_plugin`. This is recovery
of the consumed source package; publishing selected evolved graph knowledge is
not implemented in this milestone.

Search/expansion responses are bounded (default 60, maximum 200 plus explicitly
requested roots), with type, relationship, source, trust and memory filters.
Full bodies and resources load only on inspection. Backend queries currently scan
the host-owned document; they do not require a second persistence database.
The Window renders the progressive working subgraph with the host's shared
NetworkMap (Sigma/Graphology), preserving graph ownership and inspection. A
1,200-entry contract fixture verifies bounded paging; it is not a performance
benchmark of all possible graph shapes.

## Verification commands

```powershell
backend/.venv/Scripts/python.exe -m pytest backend/tests/test_matcreator.py backend/tests/test_matcreator_native.py backend/tests/test_matcreator_workspace.py plugins/matcreator/tests
$env:OAW_MATCREATOR_MANAGED = '1'
backend/.venv/Scripts/python.exe -m pytest plugins/matcreator/tests/test_native_runtime.py plugins/matcreator/tests/test_owned_engine.py
cd frontend
npx vitest run src/cards/ExecutionGraph.test.tsx src/plugins/StructureViewer.test.tsx src/legions/LegionWorkspace.test.tsx
node scripts/run-e2e.mjs --builtin-ui e2e/matcreator-native.spec.ts
```

The native runtime test uses the Pack engine/real ADK with a deterministic local
model endpoint. The browser test uses a fixture execution snapshot and real
Conversation attachment/Structure viewer APIs. These are not real-model or
remote-science acceptance tests. Generic host delegation and continuation
regressions live in `backend/tests/test_node_delegation.py` and
`backend/tests/test_node_continuations.py`, with a test-only task plugin.

Regenerate bundled source packages explicitly with:

```powershell
backend/.venv/Scripts/python.exe plugins/matcreator/build_packages.py path/to/MatCreator
```

The maintainer importer requires PyYAML. Check out the skill-package source
revision first; normal startup reads generated JSON and does not install science
dependencies. `provision_science.py` remains an optional helper for preparing ASE
inside an explicitly configured Sandbox workspace.
