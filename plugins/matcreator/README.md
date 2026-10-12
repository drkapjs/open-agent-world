# MatCreator on OAW

This plugin publishes **MatCreator native research**, a dedicated **MatCreator
Agent**, four portable Toolsets and a Know-Do Graph. Its Legion connects the
host's reusable **Agent graph** card to the native Agent.

## Native MatCreator Agent

Use **MatCreator native research** for the upstream planner → execution
orchestrator → recursive step-executor implementation. The former composed
`matcreator.research` Legion, dedicated task board, coordinator/executor prompts
and demo builder have been removed. The Pack no longer depends on Barracks.
Existing world records are not deleted or silently converted; deploy the native
preset to start a new native workspace.

The native adapter uses [AI4MS/MatCreator `devel`, revision
`0328c8b814499cbf3e49116323cee050145fa08f`](https://github.com/AI4MS/MatCreator/tree/0328c8b814499cbf3e49116323cee050145fa08f).
`native_lock.json` fingerprints its Python runtime sources. An incompatible or
modified runtime fails explicitly instead of silently falling back to an OAW
prompt-only Agent. Upstream source is a separate dependency, not copied into the
Pack or imported in the host process.

Normal OAW setup and development startup install the native runtime through the
Pack's `open_agent_world.setup` hook. Desktop builds include it in the Python
payload. No separate MatCreator install script or per-card Python path is needed.
After restarting the backend, place **MatCreator native research**, select a
default model in **Settings → Models**, and configure the connected Sandbox.

The hook installs the pinned upstream revision, orchestration dependencies and
tokenizer cache under the destination Python's `oaw-runtimes/matcreator/` directory.
It reuses verified installations, serializes concurrent setup and retries partial
installs. Hosts older than Python 3.12 get a managed 3.12 interpreter automatically.
Managed workers exclude the host's site-packages. Dependency declarations and
setup code ship inside the Pack; they are not added to the host dependency list. Normal execution
does not download packages. An incomplete installation is repaired by normal OAW
setup. The optional **MatCreator Python override** and **MatCreator checkout**
fields are only for development with a separately prepared, matching runtime.

On Windows, `scripts/dev.ps1` prepares and launches the persistent
`backend/.venv/Scripts/python.exe`, matching normal setup. A `uv run
--with-editable` overlay has its own installation prefix and cannot see that
runtime. Missing-runtime errors report the backend interpreter and expected
directory; Agent details expose the managed environment's availability.

Only orchestration dependencies are installed in this environment. Materials
libraries, CLI tools, remote-compute credentials and licensed programs belong to
the connected Sandbox. The native runtime supports ADK/LiteLLM model connections
(OpenAI, Anthropic, Gemini and legacy); the host's TypeSafe-only adapter is not
supported by the upstream execution engine.

In **normal** mode, MatCreator first presents its plan and waits for approval in
Conversation. **flash** mode delegates directly without a DAG. The Legion
connects the Agent to Conversation, Sandbox, Research knowledge, Research
artifacts. The separate **Agent graph** observes the Agent through a read-only
connection. Structure viewer follows files from
Conversation and Sandbox. No OAW Barracks or second task scheduler is deployed.

The native orchestration tools, graph validation, recursive runner, result
schemas and recovery ledger remain upstream-owned. Resource tools are replaced
at the boundary for the planner and every dynamically created child:

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

Each card/conversation pair has its own private worker state and atomic session
checkpoint under the host data root. New turns load that checkpoint in a fresh
worker; stopping a Run cancels/join its OAW tool calls and closes the worker.
Interrupted work never restarts automatically. Upstream attempt recovery runs
on the next requested turn. Private runtime paths, credentials, checkpoint data
and execution snapshots are excluded from saved Legion templates. The execution
view is a projection; an unavailable view does not abort scientific work.

Artifacts are verified through the current OAW artifact read capability, using
`oaw:artifact:<collection>:<version>` references. Sandbox paths are never treated
as host files. Native `success` and OAW Run success remain execution claims, not
proof of scientific correctness.

Validation includes host deployment/template/session/revocation tests and a
real upstream + ADK test with a deterministic local model endpoint. The latter
exercises planning, checkpoint reload, execution, recursive child tools and
final graph status. Enable it with `OAW_MATCREATOR_SOURCE` pointing at the pinned
checkout and its runtime dependencies available to the test Python, or set
`OAW_MATCREATOR_PYTHON` to an override environment's Python. Set
`OAW_MATCREATOR_MANAGED=1` to test the default Pack-managed runtime with empty
card paths after normal plugin installation. This is not
a real-model, Bohrium or scientific calculation benchmark.

## Workspace layout (revision 2)

- Left: **Agent graph**, with parent/subagent links, status, zoom, and selectable
  details. Switch to **Plan** for the native DAG and reported result summaries.
- Center: **Conversation**, **Skills & knowledge**, **Structure viewer**,
  **File preview**, **Research artifacts**, and **Research files & compute** tabs.
- Right: **Sessions** above **Files**. Both stay visible while changing center tabs.

**Agent graph** belongs to Core essentials and also works outside MatCreator.
Connect it to ordinary Agents, Agent Barracks, equipped Summoning cards or a
custom execution source, then select the source in its toolbar. Solid links mean
parent calls; dashed links mean dependencies. Runtime producers and other Packs
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
$env:OAW_MATCREATOR_PYTHON = 'absolute/path/to/installed/matcreator/python'
backend/.venv/Scripts/python.exe -m pytest plugins/matcreator/tests/test_native_runtime.py
cd frontend
npx vitest run src/cards/ExecutionGraph.test.tsx src/plugins/StructureViewer.test.tsx src/legions/LegionWorkspace.test.tsx
node scripts/run-e2e.mjs --builtin-ui e2e/matcreator-native.spec.ts
```

The native runtime test uses the real upstream/ADK with a deterministic local
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
