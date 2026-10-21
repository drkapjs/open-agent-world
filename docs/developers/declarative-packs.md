# Declarative cards

Use Schema 3 contributions for passive cards with saved configuration and an
optional independent UI. OAW derives the plugin owner, Pack and card metadata
and compiles them into the existing `PluginDefinition`, `NodeTypeDefinition`,
`PackDefinition` and `PluginRegistry`. No additional plugin manager is involved.

For a complete external project, copy
[`examples/packs/notes`](https://github.com/theAfish/open-agent-world/tree/dev/examples/packs/notes).
For lifecycle callbacks, relationships, capabilities, documents, execution,
providers or custom configuration validators, keep using the
[advanced Python API](../plugins.md).

## Minimal project

`pyproject.toml`:

```toml
[project]
name = "example-note"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = []
```

`oaw.pack.toml`:

```toml
schema_version = 3

[distribution]
id = "example.note"
name = "Note"

[[contributions.cards]]
config_schema = "config.schema.json"
```

`config.schema.json`:

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "message": { "type": "string", "default": "Hello", "maxLength": 2000 }
  }
}
```

This creates owner/card `example.note` and Pack `example.note.default`, with
the host's standard configuration editor. No Python entrypoint, wheel build or
frontend is required. When more than one card is declared, give each an explicit
`id`. Give `[plugin]` explicit `owner`, `packs` and `node_types` only when
preserving identities that differ from the defaults. Existing advanced Schema 3
declarations also accept omitted owner/Pack/backend defaults.

Each card can set `name`, `description`, `icon`, `color`, `frontend`, `size`,
`default_name`, `statuses`, `default_status`, `deck_id` and `deck_name`.
`config_schema` accepts a relative JSON file or an inline table. Paths stay
inside the project. The initial config subset supports objects, arrays,
strings, integers, numbers, booleans, null, scalar enums, numeric bounds,
string lengths/patterns and array lengths. Every property needs a valid default
for palette creation; nested objects can derive defaults from their children.
Objects reject undeclared properties unless `additionalProperties=true`.
Unknown keywords fail explicitly rather than weakening validation.

Declarative cards require Plugin API 1.37, inferred when `[compatibility]` is
omitted. Advanced plugins keep their existing compatible API versions. This
initial contribution form owns one Pack and has no Python entrypoint or
cross-plugin dependency. Use the existing advanced API for those contracts.

## Build, install and diagnose

Use the backend interpreter that belongs to OAW. From an external project,
`scripts/oaw-pack.py` is an absolute-path wrapper around the existing
`python -m open_agent_world.pack` module; it does not change the working directory.

```bash
"$OAW_PYTHON" "$OAW_PACK" doctor .
"$OAW_PYTHON" "$OAW_PACK" build-project . dist/note-0.1.0.oawpack
"$OAW_PYTHON" "$OAW_PACK" inspect dist/note-0.1.0.oawpack
"$OAW_PYTHON" "$OAW_PACK" install dist/note-0.1.0.oawpack --data-root /path/to/test-profile
```

Restart OAW to activate the installed release. In a running application, use
**Pack & Card Library > Install Pack from File**. Each build needs a new output
path; increment `project.version` before distributing an update. `build-project`
generates a standard adapter wheel and uses the same archive validator and
installer as advanced plugins.

For a custom UI, declare `[components.frontend]` and map a card's named surfaces,
for example `frontend = { body = "note" }`. Build using the distributed
`@oaw/plugin-api` package, then pass `--frontend-dir dist/frontend`. The Notes
example demonstrates independent React compilation, host configuration updates
and UI loading without rebuilding OAW.

Python dependencies belong in `project.dependencies`; runtime environments stay
in existing `[environments.<name>]` declarations. Use `resolve-adapter` and
`--dependency-wheels` for adapter dependencies. Build commands never implicitly
install into OAW. Source backend discovery uses `OPEN_AGENT_WORLD_PLUGIN_DIRS`
(parent directories containing projects); source changes require a host restart.
External frontend iteration currently uses build/install/restart in a dedicated
profile. External source frontend hot reload is deferred.

`doctor` inspects declarations without importing plugin code. `check` performs
strict activation in its own CLI process and fails if any optional plugin fails.
`verify` performs explicit integrity verification. Runtime activation failures
remain visible in the Library with their phase and repair/restart instruction;
saved cards and connections remain recoverable and cannot grant permissions.

## Validation boundaries

The Notes acceptance harness copies the project outside OAW, installs a packed
SDK, builds its UI, packages and inspects the archive, installs through the real
Library, restarts the host, edits a note, then restarts again to check identity,
configuration and geometry. CI runs this alongside the existing advanced
Greeter acceptance and strict official plugin activation/build checks.

This contribution form does not isolate Python processes or support hot reload.
Scientific plugins can retain their full SDK implementations. XRD still uses
XRD-specific Host helpers such as `runAnalysis`, `getInputs` and `stopAnalysis`;
those require a separate Host/Plugin boundary change.
