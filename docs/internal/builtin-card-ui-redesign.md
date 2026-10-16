# Builtin card UI redesign audit

Status: implementation and verification record, 2026-10-11. All active official card families have been audited and the internal UI changes propagated beyond the initial pilots. Browser acceptance and remaining specialized states are recorded separately below.

Latest follow-up: preserve Pack accent colors and remove full title/footer bars from Task Board, MatCreator Tasks and Structure Viewer. See the [new screenshot comparison](../../.outputs/content-surfaces/comparison.html) and the follow-up record at the end of this document; the first-pass screenshots below remain historical evidence.

## Scope and source of truth

This pass addresses the internal content of official cards. Canvas card faces, materials, custom artwork, plugin contracts, execution semantics and persisted workspace layout are separate ownership boundaries. No workspace-specific selector is used to make a particular research workspace look better.

The runtime snapshot at `.outputs/builtin-cards/catalog.json` contains **54 node types from 13 registered providers**, including core. The source tree contains **14 named plugin directories**; generated `__pycache__` directories are excluded from that count. Nine directories have plugin frontend modules. Barracks, Skills and generic Tasks use host-owned frontend components, so absence of a plugin frontend directory does not mean absence of UI.

Two named directories, `atomsculptor` and `literature`, contain only local Python cache remnants in this checkout. Neither has tracked frontend implementation or registered node types in this snapshot. They are recorded below rather than treated as active, unreviewed card packs.

The checkout already contained the previous design-system/composable-workspace work. This document describes the subsequent builtin-content pass; it does not claim that pre-existing layout algorithms, tokens, primitives or tests were introduced by this pass.

## Prioritized findings

| Priority | Affected content | Finding | Decision |
| --- | --- | --- | --- |
| High | Conversation/Sessions, Sandbox/Files/Terminal | Host section headings plus internal headings and several navigation rows displaced messages, files and terminal content. Three cramped columns did not adapt to narrow containers. | Reorganize internal rails and tools, use compact view selection at small sizes, retain stable component ownership and drafts. |
| High | Knowledge documents/schema/projections/experiments, MatCreator KDG | Repeated pane titles, bordered subpanels and competing metadata/configuration obscured documents and graph content. | Contextual toolbar, content-first pane, shared empty states, existing shared knowledge-map renderer. |
| High | MatCreator Tasks | Task-count/status chrome repeated grouped content; empty state used a decorative process strip; details resembled nested windows. | One actionable board header, grouped tasks, quiet details, shared loading/empty state. Acceptance review remains separate from execution success. |
| High | Visualization, Structure Viewer | Permanently expanded field controls or fixed minimum viewport heights crowded usable plot/structure space. | Separate source selection from disclosed chart configuration; flexible viewport and useful loading/error recovery. |
| High | XRD Library, Match controls/results | Expanded NAS decoration, repeated numbered sections and nested surfaces competed with inputs and scientific results. | Six plain source rows, single workflow hierarchy, plot before secondary metadata, flat parameter/result regions. |
| Medium | Library paper/reader/study | Repeated paper name, oversized stats/cover treatment and two reader toolbars competed with excerpts and pages. Study defaults assumed a dark green heading and lacked an actionable empty state. | Compact document summary, annotations as primary content, one wrapping reader control row, theme-aware study defaults and return-to-reader guidance. |
| Medium | Agent/Codex, generic Tasks, SQLite, artifacts | Always-visible capabilities, runtime diagnostics, destination configuration and repeated headings made primary actions harder to identify. | Retain primary editing/run/result controls; disclose lower-frequency settings, provenance and management. |
| Medium | Barracks, Skills, Factory, Text/Image | Card titles, category eyebrows and explanatory chrome repeated the host or buried authored content. | Remove repeated workspace titles, keep context-specific descriptions, simplify section boundaries and secondary relationships. |
| Low / retained | XRD timeline/frame canvas, Library transition, workflow/artwork containers, role cards | Some boundaries are functional: navigation between real workflow steps, scientific comparisons, draggable study cards, document transition lifecycle, host layout and authored card art. | Keep their semantics; only remove demonstrably redundant decoration or improve shared surfaces. Do not flatten distinct functions into one ambiguous view. |

## Complete runtime card inventory

“Changed” includes cards using a shared renderer changed in this pass; it does not mean every type needed its own new component. “Retained” records an intentional boundary, not a missing audit. Browser coverage is recorded separately.

| Provider / count | Exact registered node types | Internal UI owner | Disposition |
| --- | --- | --- | --- |
| Core content / 7 | `environment`, `compute-target`, `core.artifact-collection`, `agent`, `conversation`, `text`, `image` | `frontend/src/cards/ExecutionConfiguration.tsx`, `Artifacts.tsx`, `AgentCard.tsx`, `ConversationWorkspace.tsx`, `TextCard.tsx`, `ImageCard.tsx` | Changed. Configuration gets one semantic section; content, thread and primary actions get priority; supplementary details use disclosure. |
| Core compute / 1 | `sandbox` | `SandboxWorkspace.tsx`, `SandboxCard.tsx`, `SandboxFileBrowser.tsx`, shared publication controls | Changed. Files/preview/terminal composition, runtime actions, settings and publication entrypoints reorganized. |
| Core host/container / 3 | `core.shadow-collection`, `core.virtual-workspace`, `legion` | `ContainerCard.tsx`, `ShadowCollectionNode`, workspace host/container renderers | Retained host/container responsibility. This pass only makes generic host title/section responsibility corrections; no Composer algorithm or persistence redesign. |
| Core role / 1 | `core.minister-role` | `MinisterRoleCard.tsx` | Retained compact role/permission presentation; there is no separate document application to flatten. |
| Barracks / 2 | `oaw.barracks`, `oaw.barracks.summoner` | `frontend/src/cards/Barracks.tsx`, including `SummoningView` | Changed shared body: workspace title removed, explanation moved beside settings, instances section appears when relevant. Summoning and retained-instance behavior unchanged. |
| Factory / 6 | `oaw.factory.pack`, `oaw.factory.face`, `oaw.factory.function`, `oaw.factory.printer`, `oaw.factory.packer`, `oaw.factory.card` | `frontend/src/factory/FactoryViews.tsx`, `PrintedCardView.tsx`; plugin entry routes to host views | Five designer views share the simplified frame, with accessible labels preserved. Printed content is retained because its face/layout is authored pack output, not redundant builtin application chrome. |
| Codex / 1 | `openai.codex.agent` | `plugins/codex/frontend/index.tsx`, host Agent body | Changed settings shell and shared Agent hierarchy. Runtime/provider behavior retained. |
| Knowledge Base / 1 | `knowledge.base` | `plugins/knowledge_base/frontend/index.tsx`, shared `NetworkMap` | Changed document, schema, projection, experiment and graph panes; see category notes below. |
| Library / 2 | `library.region`, `library.paper` | `plugins/library/frontend/index.tsx`, `PdfReading.tsx`, `StudyCanvas.tsx`, `StudyCardContent.tsx` | Changed shared paper summary, reader controls, study surfaces/empty guidance; region import and reader contracts retained. |
| Skills / 2 | `oaw.skills.skill`, `oaw.skills` | `frontend/src/cards/SkillToolbox.tsx`, `SkillFilesEditor`, `SkillDefaultsEditor` | Changed shared toolbox shell and empty state. File/default editors and saved skill data retained. |
| SQLite / 1 | `data.sqlite` | `plugins/sqlite/frontend/index.tsx` | Changed document editor/query/results hierarchy and contextual feedback; SQL/document contracts retained. |
| Structure / 1 | `science.structure-viewer` | `plugins/structure_viewer/frontend/index.tsx`, `StructureHost.svelte` | Changed stage allocation, conditional file toolbar, shared loading/empty/error states and theme-aware viewer surface. |
| Generic Tasks / 1 | `oaw.tasks` | `frontend/src/cards/TaskBoard.tsx` | Changed repeated title, empty state and placement of optional execution controls. Task CRUD and assignment preserved. |
| Visualization / 5 | `data.visualization.graph`, `data.visualization.line`, `data.visualization.bar`, `data.visualization.scatter`, `data.visualization.histogram` | `plugins/visualization/frontend/index.tsx`, shared chart/`NetworkMap` renderer | Changed shared source/configuration/stage architecture for all five modes, rather than a graph-only or line-only skin. |
| XRD / 9 | `xrd.multiphase-harness`, `xrd.spectrum-canvas`, `xrd.structure-canvas`, `xrd.library`, `xrd.analysis`, `xrd.match`, `xrd.pattern`, `xrd.reference`, `xrd.cif` | `plugins/xrd/frontend/index.tsx`, `LibraryView.tsx`, `MatchControls.tsx`, `PipelineView.tsx`, `Multiphase.tsx`, `FrameCanvas.tsx`, `CandidateStructure.tsx` | Library/inputs/matching/scientific result surfaces changed. Multiphase sections simplified. Frame timeline, independent canvases and candidate viewer retained as functional scientific views; tokenized shared styles apply. |
| MatCreator skills/toolboxes / 10 | `matcreator.core.skill`, `matcreator.core`, `matcreator.simulation.skill`, `matcreator.simulation`, `matcreator.ai.skill`, `matcreator.ai`, `matcreator.research.skill`, `matcreator.research`, `matcreator.kdg.skill`, `matcreator.kdg` | Shared `SkillToolbox.tsx`; `plugins/matcreator/frontend/index.tsx` for KDG workspace | Shared toolbox changes cover all skill/toolbox types; KDG additionally gets content-first graph/document controls. Research workspace layout itself is not a card-specific workaround. |
| MatCreator Tasks / 1 | `matcreator.tasks` | `plugins/matcreator/frontend/TaskBoard.tsx`, `tasks.css` | Changed board header, grouping/actions, details and empty/loading state. Review status remains distinct from done. |

The rows above account for 12 core types and 42 plugin types, totaling 54. Runtime provider IDs include `open-agent-world.core`, `oaw.barracks`, `oaw.factory`, `openai.codex`, `knowledge.base`, `research.library`, `oaw.skills`, `data.sqlite`, `science.structure-viewer`, `oaw.tasks`, `data.visualization`, `research.xrd` and `matcreator`.

## Plugin-directory ownership audit

| Directory | Frontend ownership | Audit result |
| --- | --- | --- |
| `plugins/agent_barracks` | Host `Barracks.tsx` | Covered by shared host redesign; no duplicate frontend system introduced. |
| `plugins/atomsculptor` | No tracked source/frontend in this checkout | Only local cache remnants; no registered card to migrate or visually validate. |
| `plugins/card_factory` | Plugin entry plus host factory views | Designer frames covered; generated/custom artwork intentionally retained. |
| `plugins/codex` | Plugin settings plus host Agent | Covered; execution system unchanged. |
| `plugins/knowledge_base` | Plugin frontend plus shared map/document primitives | Covered across all internal views, not just the graph tab. |
| `plugins/library` | Plugin paper/reader/study/region UI | Covered; reader transition explicitly reviewed and retained. |
| `plugins/literature` | No tracked source/frontend in this checkout | Only local cache remnants; Library and Knowledge Base own active literature/document UI. |
| `plugins/matcreator` | Plugin KDG and Tasks plus host skills | Covered across both bespoke applications and shared skill/toolbox cards. |
| `plugins/skill_packages` | Host SkillToolbox/files/default editors | Covered through shared shell; editor-specific controls retained. |
| `plugins/sqlite` | Plugin frontend | Covered. |
| `plugins/structure_viewer` | React application shell and Svelte/MatterViz viewport | Covered without replacing the scientific viewer. |
| `plugins/task_board` | Host generic TaskBoard | Covered independently of MatCreator Tasks. |
| `plugins/visualization` | Plugin shared visualization application | Covered across five visualization types. |
| `plugins/xrd` | Plugin library/input/matching/pipeline/multiphase/frame viewers | Covered, with explicit retained-component notes below. |

## Information architecture changes

### Conversation and compute

Conversation places sessions and participants in an auxiliary rail around the thread; small containers select the relevant view instead of squeezing three complete panes together. Participant management belongs beside participants. Message content, attachments and the composer remain continuous, and empty attachments do not leave a redundant strip. Group controls remain host-relative and accessible.

Sandbox keeps runtime actions in one compact toolbar and exposes settings as a reversible view. Files, preview and terminal retain distinct responsibilities with compact navigation when needed. Publication is a discoverable action with a real dialog, so its entire destination/finalization form no longer permanently occupies the file tree. History and settings remain available.

### Knowledge and graph

Knowledge Base and MatCreator KDG use contextual controls and shared loading/empty states. Document/schema/projection/experiment views remove redundant page headings and nested panel shells. The graph itself retains available space and uses the shared `NetworkMap` renderer. Editable state-machine/workflow surfaces remain separate from relationship-map rendering.

### Tasks, agents and monitoring

MatCreator's board has one main header: plan/task counts and Add task. View selection sits with the relevant view actions. Group headings carry status/count meaning, replacing a second status-summary strip. Task details use typography and spacing; execution attempts use a quiet guide line instead of nested cards. The read-only detail/edit distinction, polling, drafts, stop/resume and explicit acceptance review are preserved.

Generic Tasks, Agents and Codex simplify shared wrappers and disclose supplementary capability/runtime information. Primary task editing, prompting, run/stop and results remain available. Artifacts foreground retained files and version status; provenance, destination configuration and destructive management are contextual disclosures, with confirmation behavior preserved.

### Documents, Library and data

Paper summaries lead with the document cover/byline, reader action and annotations. The host already names the card, so the body does not repeat the same title. Page progress and participation/study counts become supporting metadata rather than separate stat cards. The summary owns one scroller; wide containers can use an annotation grid.

Reader pagination/zoom and annotation tools form one wrapping control row. Study mode owns its own controls and a single Back to paper action; PDF paging/annotation tools are not mounted there. Returning preserves the current page. Annotation storage, selection and study operations keep their current contracts.

SQLite retains actual query/editor/results responsibilities while removing redundant title and status framing. Visualization retains source/schema selection in the main toolbar; “Fields & filters” expands axes, aggregate, graph-filter and row-limit configuration. Unconfigured cards expose configuration by default; configured charts give the remaining area to content. Loading, no-data and errors have explicit readable states and retry where applicable.

### Scientific viewers and XRD

Structure Viewer shows a file toolbar only when a file exists. Its stage flexes with the actual card and no longer enforces conflicting 300–360 px inner minimum heights. The Svelte scientific renderer uses the shared theme surface; loading, empty and error states preserve reload recovery.

XRD Library keeps six database slots but renders them as source rows with name/path/count and a contextual mount form. The expanded decorative NAS, drive insertion animation and LED/bay implementation are removed; the separate Canvas card readout is preserved.

The matching workflow now uses compact Search / Single phase / Multiphase navigation groups. All six step actions and current/completed/failed/cancelled states remain accessible. Numbered tiles, decorative fork connectors and continuously animated activity waves were removed from the navigation DOM, and unused four-step CSS was deleted. Input sources precede algorithm settings. The Library focus dialog owns only its direct title header, so its embedded statistics no longer inherit a second window title style.

XRD matching has one workflow hierarchy. Parameter groups no longer repeat inner 01/02/03/04 headings. Results put the experimental/reference plot before candidate comparisons; search counts, timings and provenance live under “检索概况与数据来源”. Input cards expose file selection in workspaces as well as drag/drop, preserving source downloads and CIF association. Candidate selection, run stages, residuals, scientific disclaimers and all algorithm parameters remain intact. Spectrum series switches wrap beneath the toolbar in narrow cards and use a side rail in wider cards, so visibility controls remain discoverable without expanding the plot.

## Reviewed components intentionally retained

| Component | Decision and reason | Verification boundary |
| --- | --- | --- |
| Library `StudyCardContent` | Keep draggable card/title/collapse/color responsibilities. Default heading uses theme tokens; saved explicit title colors remain user content. Empty comment paragraphs are removed and screenshot excerpts have alt text. | Real populated/empty Study images reviewed and annotation persistence passed. Large-map dragging/performance was not newly benchmarked. |
| Library `StudyCanvas` | Keep pan/zoom, per-card positioning, page branches, rename, automatic arrangement and saved color intent. Add actionable empty guidance; return-to-reader preserves the current page through an optional callback instead of navigating to page 1. | Typechecked and reader entrance regressions pass; no claim that all large-study-canvas interactions were newly benchmarked. |
| Library `GlassTransition` / reader entrance lifecycle | Retain the existing open/close transition tied to reader readiness, cancellation, inert content and reduced-motion/performance fallbacks. It is a lifecycle boundary, not a second toolbar or a decorative wrapper added by this pass. | Unit tests and all seven browser transition tests passed, including actual canvas rendering, cancellation, retry, fullscreen return and reduced motion. |
| XRD `MultiphasePanel` in `Multiphase.tsx` | Preserve search, combination/algorithm selection, fitting, review, history and scientific result details. Remove nested settings/detail surface borders and allow algorithm option groups to wrap. | 28 focused multiphase tests pass; complete populated multi-phase real-engine/browser coverage is not claimed. |
| XRD `NewWorkflowDialog` | Keep the single modal boundary and confirm/cancel safety semantics. Remove the decorative icon and repeated product eyebrow; file metadata no longer has its own framed box. | Real import, cancel/error preservation and light/dark dialog screenshots passed and were reviewed. |
| XRD `FrameCanvas` / `SpectrumCanvas` | Keep timeline/source/frame/phase controls because they select real data. Preserve zoom/pan, save, expand, per-series visibility, source linking and scientific score explanations. Responsive plot coordinates replace the fixed viewBox; PNG export preserves theme and bounds dimensions. | Real import/cursor labels and 360/1060 light/dark spectrum screenshots passed. Populated linked scientific frame/structure acceptance remains a separate runtime boundary. |
| XRD `CandidateStructure` | Retain scientific structure rendering and download/open actions. Shared result surfaces change around it. | No claim of a real external structure retrieval or fresh scientific runtime execution. |
| Host containers / authored Factory output | Preserve card art, custom printed UI and layout ownership; simplify only generic repeated host chrome. | Existing layout/ownership tests belong to the root regression run, not the pack-focused totals below. |

## Shared design system and cleanup

Reused existing semantic typography, spacing, surface, border, status, radius and motion tokens in `frontend/src/theme.css`. Production content reuses `WorkspaceToolbar` and `WorkspaceEmptyState` from `frontend/src/workspace/WorkspacePrimitives.tsx` via the SDK or host import as appropriate. `WorkspacePanel` and `ApplicationSurface` remain available from the preceding design-system work; this pass does not add them as redundant wrappers. No competing component library was introduced.

`WorkspaceEmptyState` now serves relevant conversation, task, visualization, structure, artifact, skill and document/study states. Labels, controls and native disclosure semantics remain in their content owners; a custom scientific viewer is not wrapped in a new universal card shell. `WorkspaceSection` adds an additive `isDetached(id)` query so a card can keep its detached content mounted while switching its own navigation. Permission filtering still controls whether the content can mount. This corrects ownership without modifying layout algorithms or saved positions.

Removed confirmed obsolete task status/empty-process styles, Visualization empty-icon treatment, Structure fixed-height constraints, XRD NAS/bay/drive/LED animation styles and XRD selectors that modified host workspace-window chrome. Updated remaining styles at their original definitions rather than adding a workspace-specific override sheet. Remaining scientific styling expresses plot series, authored annotation colors or specialized interaction, not a replacement global palette.

No dependency was added for this pass. Agent execution, Tools, Sandbox security, state-machine behavior, document revisions, plugin configuration and public APIs remain outside the redesign scope.

## Verification evidence

### Automated checks

The final full frontend command is `npm test` (`vitest run src`): **163 files / 1045 tests passed**. Relevant coverage includes draft preservation, detached-section permissions/lifecycle, conversation and Sandbox navigation, task execution history, empty Barracks, missing plugin fallback, SQLite editing, Visualization disclosure persistence, responsive XRD pointer coordinates/observer cleanup and bounded PNG export.

`npm run build` (TypeScript and production Vite bundle), `npm run build:pack-sdk` and `git diff --check` passed. Vite still reports large chunk warnings. No dependency was added. `python scripts/docs.py check` could not run because this environment lacks the optional `mkdocs` package; this is not reported as a documentation build pass.

Browser checks use the repository runner with Edge (`PLAYWRIGHT_CHANNEL=msedge`) and an isolated database. The `--builtin-ui` fixture uses real application APIs and components. It changes only the copied MatCreator preset's Sandbox network setting for this Windows test environment; production presets remain unchanged. Agent execution uses the runner's mock runtime. Sandbox interaction tests that intercept readiness are identified below; they do not prove a real broker or external command run.

| Browser area | Observed result and boundary |
| --- | --- |
| Standalone matrix | Agent, MatCreator Tasks, SQLite, Visualization, Structure and Artifacts: Canvas plus 360/1060 px light/dark captures passed. |
| Composed workspace matrix | All five tests passed: MatCreator Research, Knowledge Research, non-chat resize/scroll, direct composition and graph controls at 280/360 px. Includes populated documents, task review, empty states, detaching Sources and persisted manual sizing. A final long detached-document scroll check is recorded below. |
| Conversation/compute | Real session/message/draft and real Sandbox file captures passed at 360/1060 px in both themes. Conversation independent scrolling and host-relative menus passed at wide/narrow/short dimensions. Two Sandbox UI tests passed using mocked readiness responses. |
| Barracks/Skills | Two real API editor tests passed: edit/save/close/reopen policies and instructions, create/edit/save skills, 360/1060 px in both themes. All five Barracks and three Skill Toolbox interaction cases passed across the final reruns, including summoning, capability connections, equipment identity, reclamation, import/export, nested containers and delete/undo/redo. The last equipment case uses actual Zoom out controls because of the separate Fit view limitation recorded below. |
| Knowledge Base | Real HTTP upload/conversion/groups and private credential save/reopen/remove passed. No MinerU external service call is claimed. |
| SQLite/generic Tasks/Artifacts | SQLite CRUD/query/schema/reload, Tasks CRUD/dependencies/cycle rejection/undo and mock-executor durable results, artifact publication/retained versions/producer reclamation passed. |
| Visualization | All four tests passed: 3500-point line data, nullable Knowledge data, SQL-backed graph/bar/histogram, configuration/row-limit persistence and offline pack docs. |
| Library | Real PDF import/read passed at 640/1440 px in both themes. Empty Study and Back to paper were checked at dark/640; populated Study at light/1440 and dark/640. Annotations written through the real API persist after reloading; this is not a complete UI annotation-creation test. All seven reader-transition tests passed, including retry, cancellation, fullscreen reversal, reduced motion, DPR 2 and actual rendered pixel composition; transition tests use the existing HTTP harness. |
| Execution configuration | Real folder/file bindings passed. The final two configuration tests passed, including secret save/blank/reload/remove, invalid-update rejection, compute configuration import/persistence and live default profiles. No command execution is needed for these checks. |
| XRD | Both final browser tests passed: real document API import/drop/file input, invalid-data preservation, workflow dialog, plot pointer labels and six-slot library captures use the current `xrd.match` + `xrd.spectrum-canvas` flow. The final 360 px light/dark checks assert the experimental-series switch remains visible; 1060 px captures preserve the side rail. Legacy `xrd.pattern`/`xrd.library` cannot be directly user-created. |
| Structure | Both real Sandbox file viewing without starting a process and the broader MatterViz scenario passed. The latter exercises Conversation attachments, pin/follow, CIF/POSCAR/XYZ input, local rotation, drag/resize, selection, disconnect cleanup and picking within 2 px at zoom below and above 1. |

The batch logs are `.outputs/builtin-regression.log`, `.outputs/builtin-regression-2.log` through `-13.log`, and `.outputs/builtin-barracks-final.log`. Later reruns supersede earlier failures; overlapping tests are not added together as unique passes. This is a set of targeted browser suites and reruns, not a claim that every repository E2E test ran in one green batch. Raw failure traces/screenshots retained under `.outputs/builtin-failures-*` remain available for diagnosis.

### Actual screenshots and review

Open the [interactive before/after gallery](../../.outputs/builtin-cards/comparison.html): 11 paired comparisons and 120 final captures (95 standalone/data images and 25 workspace images). It links the original PNGs, provides side-by-side and after-only views, and includes populated data captures. The baseline is this checkout immediately before the builtin-content changes, including the preceding design-system work.

| Surface | Before | After / reviewed state |
| --- | --- | --- |
| Knowledge Research | [Light wide](../../.outputs/composable-workspace/builtin-before/knowledge-light-wide.png) | [Light wide](../../.outputs/composable-workspace/builtin-after/knowledge-light-wide.png), [dark narrow](../../.outputs/composable-workspace/builtin-after/knowledge-dark-narrow.png): navigation/title repetition reduced; the document reader has a clearer content area. [Graph controls at 280 px](../../.outputs/composable-workspace/builtin-after/graph-content-280px.png) are separate evidence. |
| MatCreator Research | [Dark wide](../../.outputs/composable-workspace/builtin-before/matcreator-dark-wide.png) | [Dark wide](../../.outputs/composable-workspace/builtin-after/matcreator-dark-wide.png), [task review](../../.outputs/composable-workspace/builtin-after/matcreator-task-review-narrow.png): task/status duplication reduced; acceptance remains explicit. |
| Agent | [360 px](../../.outputs/builtin-cards/before/agent-dark-360.png) | [360 px](../../.outputs/builtin-cards/after/agent-dark-360.png): session selector and context disclosure replace three cramped columns. |
| SQLite | [360 px](../../.outputs/builtin-cards/before/data.sqlite-dark-360.png) | [360 px](../../.outputs/builtin-cards/after/data.sqlite-dark-360.png), [populated](../../.outputs/builtin-cards/after/sqlite-card.png): the table selector replaces the permanent side shell; results use the width. |
| Visualization | [Wide dark](../../.outputs/builtin-cards/before/data.visualization.line-dark-1060.png) | [Wide dark](../../.outputs/builtin-cards/after/data.visualization.line-dark-1060.png), [3500-point line](../../.outputs/builtin-cards/after/visualization-line-dark.png): secondary fields are disclosed; chart/configuration remain reachable. |
| Conversation/Sandbox | Matrices are included in the workspace baseline above | [Conversation wide](../../.outputs/builtin-cards/after/conversation-light-1060.png), [narrow dark](../../.outputs/builtin-cards/after/conversation-dark-360.png), [files wide](../../.outputs/builtin-cards/after/sandbox-light-1060.png), [terminal narrow](../../.outputs/builtin-cards/after/sandbox-terminal-dark-360.png). |
| Skills/Barracks | No equivalent standalone editor baseline captured | [Skills editor 360 px](../../.outputs/builtin-cards/after/skills-editor-light-360.png), [Barracks settings 360 px](../../.outputs/builtin-cards/after/barracks-settings-light-360.png): narrow editor priority and stacked policy inputs; no disabled empty-agent prompt or duplicate settings toolbar. |
| Library | No complete pre-change reader/Study matrix captured | [Reader light wide](../../.outputs/builtin-cards/after/library-reader-light-1440.png), [Study dark 640 px](../../.outputs/builtin-cards/after/library-study-populated-dark-640.png): one relevant tool row per mode, authored study-card colors preserved. |
| XRD | Initial spectrum screenshot revealed fixed-viewBox shrinkage; it was corrected after image review | [Spectrum 360 px](../../.outputs/builtin-cards/after/xrd-spectrum-light-360.png), [spectrum dark wide](../../.outputs/builtin-cards/after/xrd-spectrum-dark-1060.png), [workflow dialog](../../.outputs/builtin-cards/after/xrd-new-workflow-light.png). The stage now measures its container, keeps 12 px labels and gives remaining height to the plot. |

The standalone helper adjusts only actual mounted container dimensions and neutralizes the Canvas camera for a readable 1:1 capture. It does not inject a product skin or mount separate mock components. Workspace captures exercise the real recipes and ownership. The initial light/360 standalone baseline captures caught an unsettled Canvas transform and are excluded from comparisons; stable dark/360 and wide captures are used instead. Standalone captured shell heights differ by 20 px, so these are structural comparisons, not pixel-diff assertions. Not every card has a per-card baseline; the gallery makes that distinction instead of fabricating before images.

The images were opened and inspected, not merely generated. MatCreator comparisons use the dark captures where both baseline and final empty states are settled; the initial light baseline still showed loading and is excluded from the paired gallery. Knowledge uses identical document data but the conversion-job badge can differ because work completes asynchronously; this is not counted as a design improvement. Review prompted substantive corrections to XRD chart sizing, Library Study tools, narrow Skills editing and Barracks settings. Typical content-heavy views now have fewer repeated headings and bordered levels. Empty-state whitespace is intentional where there is genuinely no data; populated views demonstrate that documents, messages, tables and plots can use the available area.

## Completion checklist and remaining boundaries

- [x] Enumerate all 54 registered types and all 14 named plugin directories, including inactive cache-only entries.
- [x] Redesign initial representative cards, inspect screenshots, then propagate shared and card-specific improvements to every active family.
- [x] Reorganize DOM/actions/navigation at the content owner rather than adding workspace-specific overrides.
- [x] Reuse the current design system, remove confirmed obsolete styles, retain card art and authored content.
- [x] Verify standalone Canvas, composed workspaces, narrow/wide, light/dark, empty and real-data states.
- [x] Preserve draft ownership, detached sections, saved settings, confirmations, task review and actual data/API flows.
- [x] Run full component tests, production build, SDK build and relevant real browser checks.
- [x] Complete the remaining browser regression reruns. Factory's printed-card activation case passed in batch 10; all three Skill Toolbox cases and four Barracks cases passed in batch 12. The final Barracks case passed separately, including Settings, multiple equipment drops and four-card owner deletion/undo/redo; its cleanup now skips records already deleted by successful redo.
- [ ] Resolve the separate Canvas Fit view limitation for equipped cards. In the recorded case, the Agent is measured at 1020 × 700 but three equipment entries have no measured size; React Flow reports `nodesInitialized=false` and leaves `fitViewQueued=true`. Actual Zoom out controls bring the window into view and the complete card interactions pass. The [geometry diagnostic](../../.outputs/builtin-failures-13/barracks-multiple/fit-view-geometry.json) and original trace are retained. This pass does not change Canvas measurement or camera algorithms and does not claim that navigating around this issue fixes it.
- [ ] Validate populated XRD linked-frame/multiphase scientific runs with the optional real XRD runtime and reference databases. Existing fixture-based regression covers workflow controls; external scientific computation is not claimed.
- [ ] Benchmark very large scientific tables/graphs and large Study canvases. This pass preserves renderer/worker cleanup and list ownership but does not claim a new end-to-end performance benchmark.
- [ ] Exercise the complete Library annotation-creation UI chain separately. Current populated Study evidence uses annotations seeded through the real API and checks persistence after reload.
- [ ] Run the optional documentation build after installing MkDocs, and validate installed desktop packaging/platform variants separately.

No active official card family was silently omitted. Intentional retained UI includes compact role permissions, host-only containers, editable scientific timelines, Library's document transition lifecycle and authored Factory output. Unverified specialized states and unresolved broader Canvas scenarios above remain explicit acceptance limits.

### Closing check record

Batch 8 passed all seven tests: the five composed-workspace checks plus both execution-configuration checks. Knowledge's added real wheel test reaches the document's Next steps section in a detached Sources pane and retains that position after switching the main card to Schemas. The real private-secret test also verifies that invalid reserved-name edits do not overwrite saved values.

Batch 11 passed both Structure scenarios, both XRD cases, all three Skill Toolbox cases and three Barracks cases. Batch 12 passed all three Skill Toolbox cases and four of five Barracks cases. Batch 13 passed both final XRD cases, including the narrow spectrum visibility controls. The final separate Barracks rerun passed in 9.1 seconds; both runner result files report `passed`. No product behavior was changed to satisfy old inspector-versus-workspace test assumptions. The final production build and SDK build passed; only Vite's large-chunk warnings remain.

## Follow-up: Pack accents and content-owned card edges

The user's review asked to retain distinctive Pack colors and go further in removing the expanded card's “forehead” and “chin”. Knowledge Base restores its original `#6b4fb0` light accent and `#c2aef2` dark accent, scoped to its own application/preview/section roots. Primary actions, selection, tabs and focus feedback use that palette, including detached content. Shared design tokens continue to own spacing, typography and neutral surfaces; shared components do not imply a single accent color.

Task Board (`oaw.tasks`), MatCreator Tasks (`matcreator.tasks`) and Structure Viewer (`science.structure-viewer`) now opt into `ui.content-surface.v1`. `ContentSurfaceControls` replaces expanded inspector header/footer DOM and standalone workspace titlebar DOM with a 32 px side edge. It contains a real drag region, a card-details disclosure for rename/status/finish/ID/removal, and direct expand/close buttons. The old bars are not mounted or merely hidden. Existing collapsed collectible faces, materials, surface states, size persistence and the Composer remain unchanged. Existing packs retain their shell unless they explicitly opt in.

Within the cards, generic Task Board combines progress/count and refresh with view/filter controls, removes the redundant progress strip and puts execution guidance inside execution settings. MatCreator Tasks removes the empty “Research plans” heading and the permanent goal/progress strip; the selected plan name and task actions share one content row. Task detail/edit mode carries its plan menu beside Back/Edit, preserving refresh and draft-conflict recovery. Structure Viewer uses a single-line file/source toolbar above a taller stage; full source names remain available as a hint. The expanded inspector clips its content to the existing rounded corners.

The [follow-up gallery](../../.outputs/content-surfaces/comparison.html) contains **11 paired comparisons and 61 final captures**: 36 standalone images (three cards × empty inspector/populated inspector/populated workspace × light/dark × 360/960 px) and 25 composed-workspace images. Standalone captures are 620 px high. The baseline was captured before this follow-up against the same real task documents and XYZ data. Capture helpers resize the actual mounted content and control only the Canvas camera; final images clear selection through the real canvas so resize handles are not mistaken for content. Baselines may retain selection outlines; that difference is not a design improvement. The pictures were inspected, and the review caught and corrected content clipping at the inspector's right-hand corners.

Validation for this follow-up:

- Full frontend suite: **163 files / 1046 tests passed**. Focused coverage includes shell opt-in/default compatibility, unchanged collectible finish, task draft restoration, explicit refresh, task acceptance and detached Knowledge ownership.
- Backend presentation tests: **16 passed**. Only presentation traits changed in the three Pack registrations; task execution and scientific processing are untouched.
- Production TypeScript/Vite build passed; existing large-chunk warnings remain.
- All **12 relevant browser cases passed across the final runs**: five composed-workspace cases, three card surface matrices, two Structure cases and two generic Task Board cases. The final card matrix rerun passed in 29.3 seconds. Real drag, rename, expand/collapse, deletion/undo, dependencies and mock execution remain covered. Structure's broader browser case retains the 2 px atom-picking assertion, pin/follow, local rotation and resize behavior.
- Knowledge browser assertions verify the exact purple accent for light/dark controls and a detached Sources pane, alongside real document ingestion and scrolling. MatCreator Research and Knowledge Research were both captured again.

Logs: `.outputs/content-surfaces-full-unit.log`, `.outputs/content-surfaces-backend.log`, `.outputs/content-surfaces-build.log`, `.outputs/content-surfaces-regression.log` and `.outputs/content-surfaces-final-visual.log`. The first browser batch's one structure-drag failure sampled coordinates before the Canvas camera settled; the final test waits for stable geometry and retains the original position-change assertion. The earlier specialized-runtime/desktop/performance acceptance boundaries still apply.

## Conversation creation regression and restored group tab

Creating a session/group could blank the entire application with React's `Maximum update depth exceeded`. Canvas and composed Workspace can each render the same conversation with separate REST snapshots and a shared participant-choice draft. While the Canvas snapshot still lacked the newly created session, its selection-normalizing effect cleared the participant chosen by the Workspace. The two copies repeatedly rewrote the same draft. Participant fallback is now derived locally; only explicit user selection writes the shared choice. No conversation API, execution or layout persistence was changed.

The user-approved top-right Group tab is restored, including its top-attached curved shoulder. New session stays at the left of the same compact row. The menu retains host-relative positioning, keyboard focus and its independent scroll area.

Evidence: the dual-owner unit regression reproduced the same crash before the fix (`.outputs/conversation-fix-unit-repro.log`). The new browser regression uses real creation endpoints and holds subsequent summary refreshes to exercise the stale Canvas snapshot deterministically. It also verifies the composer and participant target remain usable. Independent, composed, MatCreator Research and Knowledge Research creation, plus group rename/delete/drag and wide/narrow/short menus, pass in seven browser cases (`.outputs/conversation-fix-browser-final.log`). Focused component/timeline/state tests pass: 33 tests (`.outputs/conversation-fix-unit.log`). The browser fixtures use the isolated test world and mock Agent runtime; no real Agent execution is claimed.

Screenshots: [original ordinary toolbar](../../.outputs/conversation-fix/before.png), [restored tab and new group](../../.outputs/conversation-fix/after-composed-light.png), [dark](../../.outputs/conversation-fix/after-composed-dark.png), [narrow menu](../../.outputs/conversation-groups-narrow.png). These show actual mounted application UI, not a mockup; the before image uses the original General group while the after image shows a newly created group.
