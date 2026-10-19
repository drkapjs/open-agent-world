# Development setup

**Goal:** run the source checkout with a separate profile for your plugin experiments.

## Prerequisites

Install Git, Python 3.11 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 22 or newer. Python is enough for the plugin code; Node builds and serves OAW's frontend.

Windows, Linux/WSL and macOS support local Sandbox execution with their required isolation runtime. macOS has a built-in Seatbelt fallback; Apple Container 0.6.0+ on macOS 26+ Apple Silicon provides the preferred VM boundary.

## Clone and install

```sh
git clone https://github.com/theAfish/open-agent-world.git
cd open-agent-world
```

Use the branch matching the documentation you are reading. This site's development guides track `dev`:

```sh
git switch dev
```

Windows PowerShell:

```powershell
./scripts/setup.ps1 -Profile plugin-tutorial -SkipBuild
./scripts/dev.ps1 -AgentRuntime mock -Profile plugin-tutorial
```

Linux, WSL, or macOS:

```sh
bash scripts/setup.sh --profile plugin-tutorial --skip-build
bash scripts/dev.sh --agent-runtime core.mock --profile plugin-tutorial
```

Open the URL printed by the launcher. Ports can change when the usual ones are occupied. Keep the launcher running and stop it with Ctrl+C.

Setup is the installation entrypoint on every platform, backed by `scripts/setup.py`.
Run it when creating a profile or changing dependencies. `--skip-build` / `-SkipBuild`
prepares the SDK and Pack dependencies without building the production frontend.
Ordinary `dev` launches only check the environment and build source assets; they do
not run uv sync, editable installs, setup hooks, or npm ci. Missing or stale
dependencies produce an error pointing back to setup. Frontend builds and hot reload
still run on launch and source edits; this is compilation, not package installation.
Setup and dev both accept Node 22+. The current `matterviz` dependency declares
Node 24+, so Node 22 displays a compatibility notice instead of blocking setup.
This does not change the dependency's own engine declaration.

Additional trusted plugins use `-PluginPath @('path/to/plugin')` on PowerShell or
repeatable `--plugin-path path/to/plugin` on the portable scripts. Pass them to setup
and dev; setup includes the repository plugins as well. Both use the persistent
`backend/.venv` interpreter. The bare setup command prepares production data for
`start`; development Pack environments belong to the explicitly selected profile.

The mock runtime is for deterministic debugging; it is not a language model. The named development profile keeps these experiments separate from your normal app data. Reuse the same profile to keep your test world.

## Locate the public APIs

| Code you write | Import from |
| --- | --- |
| Python contributions | `open_agent_world.plugin_api` |
| React views | `@oaw/plugin-api` |

Application internals are useful for debugging the host, but plugin implementation should use these public imports. Integration tests can use host services to verify discovery and persistence.

Continue with [Your first plugin](first-plugin.md). For host development and broader checks, see [Run from source](../getting-started.md#development-and-verification).
