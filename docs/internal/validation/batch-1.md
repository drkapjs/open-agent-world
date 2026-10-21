# Batch 1: optional plugin startup and declarative development

Base: `dev` at `7cf8a8211c0377d55075b58d7ed44cc6406b8edb`, the merge of
[Batch 0 / PR #43](https://github.com/theAfish/open-agent-world/pull/43).

## 1A audit and implementation

Core is the `open-agent-world.core` registration: world persistence, native card
and relationship contracts, permissions, conversations, execution services,
Sandbox interfaces, HTTP/UI and Pack installation. Its 14 card types do not
require any package under `plugins/`. ADK and LiteLLM are optional runtime SDKs;
their adapters remain lazy. Core initialization, database corruption, ownership
reconciliation and resource-accounting failures must remain visible failures.

All 13 official source plugins already have Schema 3 declarations. The existing
installer already owns dependency wheels, environments, immutable releases,
receipts, recovery and resource leases. `PluginRegistry.install` already stages
contributions. `WorldStore` already retains unavailable cards/edges and the
Capability broker already excludes their grants. No replacement manager,
permission layer, Sandbox or parallel contribution registry is needed.

| Path | Before | After 1A |
| --- | --- | --- |
| Source discovery | Adds import paths during discovery; one invalid manifest aborts | Static pass before import paths; per-source diagnostics |
| Source activation | Adapter/import/registration failure aborts the process | Failing owner is unavailable; independent owners load |
| Dependency order | All factories count as available before registration succeeds | Only committed registrations satisfy downstream dependencies |
| Registration | Sandbox requirements can fail after contributions commit | All loader validation precedes commit; Pack catalog construction is staged |
| Installed Packs | Whole selection and all startup receipts checked as one fatal operation | Existing manager checks each ready candidate against loaded selections; failed dependency consumers remain unavailable |
| Retained resources | Reconciliation assumes discovery is complete | Partial discovery retains existing source claims and defers collection |
| Development preflight | Requires every editable plugin and prepared environment | Core prerequisites only; loader owns optional diagnostics |
| Vite | One optional build or manifest can stop the host | Dev reports the failing Pack and serves other artifacts; stale failed assets return 404 |
| Setup | Full optional preparation can stop Core setup | `--core-only` omits optional Python/Pack installation; normal setup reports independent optional failures |
| CI | Product startup also acts as the strict gate | Explicit strict activation plus strict frontend builds; separate clean Core HTTP probe |

`GET /api/packs` exposes failure origin, phase and a repair/restart action. The
Library displays unavailable entries even when no plugin code registered.
Existing restore/install/retained-version activation operations perform explicit
preparation; restart retries activation. There is no in-process hot reload.
Startup does not install packages or silently repair corrupt receipts.

Run `scripts/setup.py --core-only` for minimal setup and set
`OPEN_AGENT_WORLD_CORE_ONLY=1` when starting a deliberately Core-only source
checkout. Normal source startup discovers optional folders even if their Python
packages were not installed editable. Missing dependencies become unavailable
diagnostics. Existing full setup remains supported, including optional runtime
extras. Ordinary dev/start remains installation-free.

## Validation

The fresh `.tmp/batch1-core` interpreter installed only the 25 applicable locked
Core distributions, using the existing uv cache after network downloads stalled.
It contained no plugin entrypoints, ADK, LiteLLM, NumPy, ASE or PyMuPDF.
`scripts/check-core-startup.py` started a real uvicorn child process and read
`/api/catalog`, `/api/packs` and `/api/world` over localhost HTTP: passed, with
14 Core card types. The script shuts the server down through its lifespan and
also runs in CI in a separate minimal environment.

Fault-injection tests stage a card, relationship and Capability before raising;
assert that none are registered; reopen the same SQLite workspace; verify saved
configuration/geometry and IDs, missing placeholders and absence of grants; then
repair the plugin and verify recovery. Tests also cover discovery/import errors,
dependency consumers, strict failure reporting and fatal Core/database failures.

Focused backend checks passed for loader, registry, installed startup, installer,
source ownership, missing plugins, source launchers and lifecycle recovery.
The real Vite test serves an independent artifact while a sibling build fails,
and verifies that the same defect fails a strict build. Card Library and plugin
surface tests passed (26 cases); TypeScript checks passed.

A lifecycle recovery test exposed the local `pymupdf4llm` version (1.28.2)
conflicting with Knowledge Base's `<0.4` declaration. That plugin is correctly
unavailable; this must not block restoring unrelated Barracks. The restoration
check now considers loaded source adapters plus the explicit recovery target.
No host dependency was downgraded to conceal this condition.

## Migration inventory and follow-up boundaries

| Official source | Category | Existing IDs and migration decision |
| --- | --- | --- |
| `agent_barracks` | Medium | `oaw.barracks`, 2 types; retain container behavior |
| `skill_packages` | Medium | `oaw.skills`, 2 types; retain lifecycle/state behavior |
| `task_board` | Medium | `oaw.tasks`, 1 type; retain document/execution behavior |
| `codex` | Complex runtime | `openai.codex`, 1 type; retain Runtime Provider and client environment |
| `card_factory` | Complex | `oaw.factory`, 6 types; retain content authoring |
| `sqlite` | Medium | `data.sqlite`, 1 type; retain data and query contracts |
| `structure_viewer` | Simple UI with advanced metadata | `science.structure-viewer`, 1 type; suitable for manifest defaults, preserve tutorials/deployment |
| `visualization` | Medium | `data.visualization`, 5 types; retain data consumer/remapping contracts |
| `library` | Complex research | `research.library`, 2 types; retain PDF/document data |
| `knowledge_base` | Complex research | `knowledge.base`, 1 type; PDF adapter dependency requires explicit preparation |
| `atomsculptor` | Complex research | owner `atomsculptor`, Pack `atomsculptor.default`, 4 types; retain 11 environments |
| `matcreator` | Complex research | owner `matcreator`, Pack `matcreator.default`, 11 types; retain 7 environments |
| `xrd` | Complex research | `research.xrd`, 9 types; private Host imports still block independent publication |

The batch does not change existing card, relationship or Pack IDs, saved config
formats, layouts or schema migrations. In-process Python cannot undo arbitrary
module side effects or isolate incompatible native libraries. Registry atomicity
is a contribution guarantee, not an independent-process host. Legacy shared
Sandbox Python still prepares one shared dependency set; its affected owners
share an environment failure, without stopping Core or unrelated owners. A
separate Host/Plugin boundary batch is required for XRD and stronger runtime
isolation. Real external models, cloud execution, macOS and installed desktop
acceptance are not established by these local checks.
