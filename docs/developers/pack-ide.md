# Pack IDE: one source project

Open **Pack & Card Library → Pack IDE** to create a visual Card project or open
an existing Schema 3 source directory. Design, Function, Code and external
editors all work on that directory. The IDE does not recover source from a
compiled `.oawpack`.

`oaw.pack.toml` owns distribution identity, compatibility, dependencies,
contributions and optional backend/frontend components. `pyproject.toml` owns
the version and Python build metadata. Python, React/TypeScript, assets and
documentation remain ordinary files. The IDE only stores chosen project roots
in the existing local profile settings; it does not store a second project
representation.

## Visual authoring

Create a project, select its Card, and edit appearance in **Design**. **Function**
edits text, numeric and boolean inputs and the existing bounded template, join,
sum and multiply operations. The same Face Designer data and Function Designer
models are used by the runtime. Save writes a structured JSON resource. Check
reports static errors without importing the project's Python. Preview exercises
bounded declarative functions; it does not establish installed runtime readiness.

Build / Export calls the canonical Schema 3 builder. New visual-only projects
receive the existing generated adapter wheel and use the ordinary installer and
PluginRegistry. They do not require an independently compiled frontend. Export
produces a `.oawpack`; the existing Marketplace handoff becomes available after
export for non-`local.*` IDs. The user signs in and selects the exported file on
the Marketplace website. Opening that page does not upload or publish a Pack.

## Binding existing Python behavior

Plugin API 1.39 adds a presentation resource mapping to the source declaration:

```toml
[compatibility]
plugin_api = "1.39"

[designs]
"example.analysis-card" = "designs/card.json"
```

The referenced JSON contains `face` (the existing FaceDesign), `function` (the
existing FunctionDesign), `action`, `result_key` and `surfaces`. An advanced
card's `action` names an existing `NodeResourceAction`, for example
`analyze_structure`. Input keys become that action's arguments; `result_key`
selects the returned result for display. Function button bindings can name other
registered resource actions. A missing action is rejected at registration.
The installed renderer dispatches through OAW's ordinary node resource endpoint,
with the existing authorization and cancellation boundaries.

Presentation overlays are applied after the advanced Python registration hook.
They retain its behavior, state, lifecycle, relationships, capabilities and
execution definitions. They only replace the explicitly selected frontend
slots. Keep a handwritten React `workspace`, for example, by omitting
`workspace` from `surfaces`; an empty list preserves every custom frontend slot.
Complex JSX remains a Code/external-editor workflow.

The [Cell Analysis example](../../examples/packs/analysis-card/README.md) shows
the complete mixed path. Appearance edits only write JSON; they do not regenerate
its handwritten Python. Changes to a Python adapter require a rebuild and
controlled host restart. Dynamic Python registrations cannot be fully understood
by static inspection; an installed test profile validates their actual contracts.

## External tools and Agents

Code edits operate on actual project files. Every read supplies a SHA-256 hash;
save requires the hash that was read. Check and Build also compare the complete
project revision. External changes cause a conflict instead of silently replacing
newer files. Refresh to inspect the changed source, compare it with the current
draft, and revise the draft before saving.

An OAW Agent uses its existing authorized workspace/file/Sandbox tools on the
same files. A host project handle does not grant an Agent filesystem permission
or permission to execute generated code. External coding agents can edit the
directory directly and use the standard CLI:

```sh
python /path/to/OAW/scripts/oaw-pack.py doctor /path/to/project
python /path/to/OAW/scripts/oaw-pack.py build-project /path/to/project /path/to/result.oawpack
python /path/to/OAW/scripts/oaw-pack.py inspect /path/to/result.oawpack
python /path/to/OAW/scripts/oaw-pack.py project inspect /path/to/project
python /path/to/OAW/scripts/oaw-pack.py project read /path/to/project --path designs/card.json
```

`project write` accepts `--path`, `--content-file` and the `--expected-sha256`
returned by read. Use `init /path/to/new-project --visual` to create the same
visual source skeleton from the command line. For an OAW Agent, choose a project
directory already inside its granted workspace; the default local-profile
project directory is not automatically shared with Sandboxes.

For advanced projects, build Python and frontend code in your external editor
or authorized development environment, then pass `--backend-wheel` and
`--frontend-dir` to `build-project`. The IDE accepts the same prebuilt inputs.
It never runs a project's build hooks or imports its source in the main host.
Use `check --lifecycle` only for trusted code in a disposable development process
and profile; unlike `doctor` and IDE Check, this command loads plugin code.
Never use a production profile to test unreviewed code.

## Compatibility and verification boundaries

Existing Factory devices and saved designs remain available as advanced graph
interactions. Existing Schema 2 content Packs use their existing consumer path.
The new direct project workflow emits Schema 3; Schema 1 stays unsupported.
The installer, ownership model and Marketplace authentication are unchanged.

The implementation's focused acceptance includes a generated visual adapter,
a mixed Python adapter installed into a clean profile and invoked in a fresh
process, source preservation, project conflicts and path restrictions. Browser
acceptance separately exercises the IDE controls and ordinary installation.
A browser test of a local host is not installed Tauri/WebView2 desktop evidence.
Consult the delivery report for the tests and native environments actually run.
