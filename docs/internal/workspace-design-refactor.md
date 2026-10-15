# Unified workspace design implementation

This record tracks the October 9, 2026 audit and implementation against the supplied OAW Unified Design System & Composable Workspace Refactor proposal.

## Audit and preserved contracts

- Keep `theme.css` as the semantic palette and keep the existing light/dark preference. Card stock, ink, foil, printed designs and canvas LOD remain independent of application content.
- Keep the v2 split/pane/tabs layout tree. It already supports nested horizontal/vertical divisions, docking, tabs, editable ratios and saved layouts. Grid-like mosaics can be represented by nested splits; a second layout engine is unnecessary.
- Keep `WorkspaceSection` portals: moving a section moves its DOM host, while the card retains its React owner, drafts, subscriptions and runtime state.
- Keep draft/baseline layout editing, cancel/save and expected-revision conflict handling. Initial composition must be explicit; events and content refresh must not rearrange the workspace.
- Keep unplaced cards accessible through existing drawers. Automatic recipes must include every supplied view and may not create tabs or hide content.
- Preserve plugin compatibility without metadata; metadata describes sizing, never a required position or an implicit chat-first priority.

## Confirmed gaps

| Area | Finding | Change |
| --- | --- | --- |
| Visual system | Palette exists, but type/spacing/motion scales are scattered | Extend existing tokens and reuse controls; add small public content primitives |
| Conversation | 10px text and percentage columns compress at narrow widths | Readable tokens and container-sized stacking |
| Files and terminal | Small labels and no local breakpoint | Local container rules and toolbars that wrap |
| Knowledge | Mature document workflow; 420px root floor and detached sections bypass container | Preserve reader focus, reduce minimum and give moved sections local containers |
| Graph | Fixed toolbar widths react only to browser width | Graph container query and reachable overflow controls |
| Composer | Fixed 240×200 minimum; no reusable size-aware starter | Optional sizing metadata and deterministic v2 recipes |

## TODO

- [x] Phase 0: audit rendering, state ownership, plugin and persistence contracts.
- [x] Phase 1 contract: optional public presentation sizing metadata; focused backend validation.
- [x] Phase 1 visual: shared tokens, content primitives and existing control integration.
- [x] Phase 2: responsive Conversation, Sandbox, Knowledge and graph toolbar.
- [x] Phase 3: sizing-aware composition, preserved saved layouts and edit/runtime presentation.
- [x] Phase 4: public recipes, reusable content examples and developer usage documentation.
- [x] Phase 5: MatCreator, Knowledge and non-chat representative visual validation.
- [x] Phase 6: focused regressions, production build, screenshots and cleanup review;
  the existing MatCreator canvas-entry failure remains recorded below.

## Validation boundaries

The final frontend suite passed **1,030 tests in 160 files**. Backend presentation,
plugin registration, Legion workspace, MatCreator workspace, Visualization and
Knowledge tests passed **60 tests**. Production frontend and Pack SDK declaration
builds passed. Vite retains its large-chunk warning.

An independent Pack frontend built and rendered the new helpers using one host
React instance; its 1,656-byte output has only host SDK/JSX references and fixture
code. Evidence: `.tmp/oaw-design-pack/result.json`. This verifies the frontend
build contract, not an installed desktop Pack lifecycle.

The final visual suite passed **5 tests**: representative workspaces in light/dark,
wide/narrow and edit/runtime states; empty and populated documents/graphs;
non-chat one-shot composition; saved split resize/reopen; detached Sources;
280px/360px graph entry creation; and narrow terminal command scroll reachability.
The MatCreator matrix case was then strengthened and passed again: real UI plan
and task creation/save, a real HTTP transition to `review`, and close/reload/reopen
preserve acceptance criteria and the human-review reminder. Successful execution
is not automatically promoted to accepted/done. This is part of the five matrix
cases, not a sixth independent test.
Browser fixtures use an isolated profile and mock Agent runtime; they do not
validate real model, cloud or Sandbox execution.

The existing Legion workspace regression passed **10 tests** (57.5s), including
nested splits, keyboard/mouse resizing, detached/hidden section recovery, live
drafts, save failure/retry/discard, blueprint capture and fresh deployment, and
workspace preferences. Fixture setup now explicitly equips a collected blueprint
in its Deck, accepts the expected owned-Pack confirmation, and reloads after
direct API setup to synchronize revisions. These preserve the independent
Library/Deck/collection contracts rather than changing product behavior.

The E2E runner accepts optional `OAW_E2E_TIMEOUT_MS` for suite-level time budgets;
its default remains 120 seconds, invalid/nonpositive values fall back to that
default, and individual test assertions/timeouts are unchanged.

The existing Knowledge workflow and the final post-padding Knowledge visual
recapture passed. The existing MatCreator preset E2E did **not** pass: at
`frontend/e2e/matcreator-workspace.spec.ts:52`, after **Fit view**, a distant
toolset's canvas node was absent from the DOM. It stopped before opening the
workspace. The test's original assertions and click behavior remain unchanged;
the separate workspace matrix does not validate this canvas framing behavior.

Three targeted card-material E2E tests passed (6.3s): light/dark readability and
tilt, 200 passive cards without continuing animation callbacks, and matte
workspace/inspector surfaces remaining independent of printed card finishes.
This is a focused performance invariant, not a comparative frame-time benchmark.
In total, **19 distinct focused E2E cases passed**, with the one existing
MatCreator canvas-entry case failing as described above. Browser validation ran
on Windows Headless Edge with actual local HTTP and isolated fixture data.

Documentation: all three changed published pages pass source checks; **16** tooling
tests, strict MkDocs build and rendered-site verification (**75 pages, 8,398 local
links/assets, 30 search pages**) pass. The all-in-one `scripts/docs.py check` still
fails at **43 pre-existing language-separation errors** in `card-factory.md` (38),
`creator-packs.md` (1), and `developers/card-design.md` (4). Those three files were
verified byte-equivalent to HEAD after newline normalization and are unchanged.

## Deliverables and use

- [Design-system and responsive content guide](../developers/workspace-design.md)
  includes semantic tokens, warm/paper appearances, public content helpers,
  Python and TypeScript sizing declarations, recipes and override guidance.
- `composeWorkspace` provides balanced, columns, rows and focus recipes over the
  existing v2 tree. The empty editor exposes the first three; Tabs/Dock remain
  direct editing operations. No additional layout library or runtime observer
  was introduced.
- API 1.28 adds optional `NodePresentation.workspace_layout`; old plugins retain
  neutral defaults. Knowledge and Visualization declare their new minimum host
  version. Existing Card Factory printed geometry is unchanged; its content can
  still participate without declaring metadata.
- Runtime application titles/tabs no longer mount decorative card finish layers.
  Single-view titles lose the redundant active-tab border, while actual multi-tab
  groups keep their selected-state indicator. Canvas finishes and card printing
  are untouched.
- Conditional Section unmounts retain their last declared sizing for the open
  workspace; explicit new declarations update it and owner removal clears it.
  Tests verify no saved layout mutation or unwanted persistence write.

## Before and after

Open the [comparison gallery](../../.outputs/composable-workspace/comparison.html)
for six matching before/after pairs. Source images are in
`.outputs/composable-workspace/before` (17) and `after` (24). Additional after
proofs show generated non-chat layouts, detached Sources, narrow graph editing
and terminal scrolling, plus preserved MatCreator task review state.

| Representative workspace | Before | After |
| --- | --- | --- |
| MatCreator | [Wide light](../../.outputs/composable-workspace/before/matcreator-light-wide.png) | [Wide light](../../.outputs/composable-workspace/after/matcreator-light-wide.png) |
| Knowledge | [Narrow dark](../../.outputs/composable-workspace/before/knowledge-dark-narrow.png) | [Narrow dark](../../.outputs/composable-workspace/after/knowledge-dark-narrow.png) |
| No Conversation | [Narrow dark](../../.outputs/composable-workspace/before/non-chat-dark-narrow.png) | [Narrow dark](../../.outputs/composable-workspace/after/non-chat-dark-narrow.png) |

## Compatibility and cleanup

Persisted layout trees, ratios, active tabs, hidden sections and card state retain
their existing format. A new minimum may enlarge an undersized old pane at render
time; this is not pixel-identical migration. Below aggregate minima the stage
scrolls, without discarding content. Short stacked Sandbox panes may require
scrolling to the command input; this was explicitly exercised in the browser.

Changes extend existing CSS and control classes. The obsolete viewport-only
NetworkMap breakpoint was replaced with local container rules. Repeated
Knowledge empty-state markup now uses the public helper. Application-only finish
layers were removed; no speculative deletion of old components or dependencies
was performed. There are no new package dependencies or Agent/Tools/Sandbox
execution/state-machine changes.

The existing editor provides live pane previews. This increment does not add a
second template editor or migrate every specialized Pack screen. No packaged
Windows/macOS/WebKit, deployed application, real-service execution or comparative
large-workspace frame-time benchmark is claimed. Screenshot tests invoke the
existing Workspace action programmatically where the pre-existing Fit view
behavior places its canvas button outside the viewport; pointer discoverability
of that button remains outside this validation.
