# OAW frontend Pack SDK

Use `FrontendPlugin` and `PluginViewProps` from `@oaw/plugin-api`.
Build the default `FrontendPlugin` export with `buildPackFrontend` from
`@oaw/plugin-api/build`. The build replaces React, JSX, ReactDOM and runtime SDK
imports with references to the host instances. Do not bundle another React,
use absolute host source imports, or render a second application root.

The host loads the resulting ES module from the installed immutable version.
Relative assets may live in `frontend/` or `assets/` in the `.oawpack`.
CSS can be included by the view using a relative stylesheet URL; importing CSS
in the entry produces a separate esbuild CSS file which the view must load.

Hosts with Plugin API 1.28 also export `ApplicationSurface`, `WorkspacePanel`,
`WorkspaceToolbar`, `WorkspaceEmptyState`, the existing `IconButton` and `Modal`,
and `composeWorkspace`. Declare Python `plugin_api_version="1.28"` when using
these helpers. They use host theme tokens and shared React, and remain optional.
See the [workspace design guide](../../docs/developers/workspace-design.md) for
content templates, optional card/section sizing and one-shot layout recipes.
