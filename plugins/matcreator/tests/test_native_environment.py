"""Installation ownership, retry and discovery without network downloads."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from open_agent_world.plugin_api import AgentConfig, AgentDependencyError, RuntimeInput, RuntimeModelConnection
from plugins.matcreator.oaw_matcreator import native_environment as environment
from plugins.matcreator.oaw_matcreator.native_runtime import MatCreatorRuntime


def test_runtime_discovery_does_not_depend_on_windows_architecture_environment(monkeypatch):
    expected = environment.environment_root()
    for key in ("PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432"):
        monkeypatch.delenv(key, raising=False)
    assert environment.environment_root() == expected


def test_missing_environment_reports_the_actual_backend_interpreter_and_path(tmp_path, monkeypatch):
    prefix = tmp_path / "uv-overlay"
    python = prefix / "Scripts" / "python.exe"
    monkeypatch.setattr(environment.sys, "prefix", str(prefix))
    monkeypatch.setattr(environment.sys, "executable", str(python))
    with pytest.raises(RuntimeError) as error:
        environment.read_environment()
    assert str(python) in str(error.value)
    assert str(environment.environment_root()) in str(error.value)
    assert "same Python" in str(error.value)


@pytest.fixture
def installer(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(environment, "environment_root", lambda: root)
    monkeypatch.setattr(environment.shutil, "which", lambda _: "uv")
    calls = []

    def run(args, *, env):
        calls.append(list(map(str, args)))
        if "--target" in args:
            for name in ("google/adk", "litellm", "json_repair"):
                package = root / "packages" / name
                package.mkdir(parents=True, exist_ok=True)
                (package / "__init__.py").write_text("")

    monkeypatch.setattr(environment, "run", run)
    return root, calls, run


def test_normal_setup_publishes_only_verified_runtime_and_reuses_it_offline(installer, monkeypatch):
    root, calls, _ = installer
    environment.install()
    assert len(calls) == 2
    assert calls[0][-2:] == ["-r", str(environment.REQUIREMENTS)]
    assert "--check" in calls[1]
    assert not any("git+" in arg for call in calls for arg in call)
    assert not (root / "packages" / "matcreator").exists()
    assert environment.read_environment() == (sys.executable, root)
    assert json.loads((root / "ready.json").read_text())["python"] == "host"
    monkeypatch.setattr(environment.shutil, "which", lambda _: None)
    environment.install()
    assert len(calls) == 3
    assert "--check" in calls[-1]  # Local validation only; no uv/network.


def test_failed_install_is_not_discoverable_and_next_setup_repairs_it(installer, monkeypatch):
    root, calls, original = installer

    def fail(args, *, env):
        original(args, env=env)
        if "--check" in args:
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(environment, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        environment.install()
    with pytest.raises(RuntimeError, match="same Python"):
        environment.read_environment()
    assert not (root / "ready.json").exists()
    monkeypatch.setattr(environment, "run", original)
    environment.install()
    assert environment.read_environment()[1] == root
    assert len(list(root.glob("packages.incomplete-*"))) == 1


def test_setup_repairs_a_corrupt_cached_environment(installer, monkeypatch):
    root, _, original = installer
    environment.install()
    checks = 0

    def corrupt_once(args, *, env):
        nonlocal checks
        if "--check" in args:
            checks += 1
            if checks == 1:
                raise subprocess.CalledProcessError(1, args)
        original(args, env=env)

    monkeypatch.setattr(environment, "run", corrupt_once)
    environment.install()
    assert checks == 2
    assert environment.read_environment()[1] == root


def test_concurrent_setup_installs_only_once(installer, monkeypatch):
    _, calls, original = installer
    entered, release = threading.Event(), threading.Event()

    def slow(args, *, env):
        if "--target" in args and not entered.is_set():
            entered.set()
            assert release.wait(5)
        original(args, env=env)

    monkeypatch.setattr(environment, "run", slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(environment.install)
        assert entered.wait(5)
        second = pool.submit(environment.install)
        release.set()
        first.result(timeout=5)
        second.result(timeout=5)
    assert sum("--target" in call for call in calls) == 1  # Third-party dependencies only; engine ships with the Pack.
    assert sum("--check" in call for call in calls) == 2


def test_receipt_relocates_with_payload_and_cannot_escape_it(installer):
    root, _, _ = installer
    environment.install()
    relocated = root.with_name("relocated")
    root.rename(relocated)
    assert environment.read_environment(relocated) == (sys.executable, relocated)
    (relocated / "ready.json").write_text(json.dumps({"format": 2, "python": "../outside.exe"}))
    with pytest.raises(RuntimeError, match="setup is incomplete"):
        environment.read_environment(relocated)


def test_older_host_gets_pack_owned_python_and_relative_receipt(installer, monkeypatch):
    root, calls, _ = installer
    executable = root / "python" / "cpython-3.12" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(environment.sys, "version_info", (3, 11, 0))
    monkeypatch.setattr(environment.subprocess, "check_output", lambda *args, **kwargs: str(executable))
    environment.install()
    assert calls[0] == ["uv", "python", "install", "3.12"]
    assert calls[1][calls[1].index("--python") + 1] == str(executable)
    assert json.loads((root / "ready.json").read_text())["python"] == "python/cpython-3.12/python.exe"
    relocated = root.with_name("relocated")
    root.rename(relocated)
    assert environment.read_environment(relocated)[0] == str(relocated / "python/cpython-3.12/python.exe")


def test_worker_dependency_path_excludes_host_site_packages(tmp_path):
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / "private_probe.py").write_text("value = 'pack-owned'")
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from native_environment import activate
activate(Path(sys.argv[2]))
import private_probe
print(json.dumps({'value': private_probe.value, 'paths': sys.path}))
"""
    value = json.loads(subprocess.check_output([sys.executable, "-I", "-S", "-c", script,
                                               str(environment.PACKAGE), str(tmp_path)], text=True))
    assert value["value"] == "pack-owned"
    assert not any("site-packages" in path for path in value["paths"])


@pytest.mark.asyncio
async def test_missing_runtime_never_falls_back_to_host_or_installs_during_run(installer, monkeypatch):
    _, calls, _ = installer
    provider = SimpleNamespace(
        resolve_model=lambda _: RuntimeModelConnection("openai", "test", "http://127.0.0.1", "secret"),
        runtime_state_directory=lambda _: environment.environment_root().parent / "state",
    )
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig("agent", "Native")
    await runtime.create_agent(config)
    info = await runtime.get_agent("agent")
    assert info.details["environment"]["status"] == "missing"

    async def no_worker(*args, **kwargs):
        pytest.fail("An incomplete installation must not start a worker")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", no_worker)
    with pytest.raises(AgentDependencyError, match="same Python"):
        async for _ in runtime.execute(config, SimpleNamespace(context_id="session", run_id="run"), RuntimeInput("hello")):
            pass
    assert calls == []
    assert runtime.active == {}


@pytest.mark.asyncio
async def test_default_worker_uses_managed_environment_and_utf8_protocol(tmp_path, monkeypatch):
    from plugins.matcreator.oaw_matcreator import native_runtime as runtime_module
    worker = tmp_path / "native_worker.py"
    worker.write_text('''import json, sys
request = json.loads(sys.stdin.readline())
assert sys.flags.isolated and sys.flags.no_site and sys.flags.utf8_mode
assert sys.argv[1] == '--environment'
print(json.dumps({'type': 'completed', 'text': '\\u4f60\\u597d'}, ensure_ascii=False), flush=True)
''', encoding="utf-8")
    monkeypatch.setattr(runtime_module, "__file__", str(tmp_path / "native_runtime.py"))
    monkeypatch.setattr(runtime_module, "read_environment", lambda: (sys.executable, tmp_path / "managed"))
    provider = SimpleNamespace(
        resolve_model=lambda _: RuntimeModelConnection("openai", "test", "http://127.0.0.1", "secret"),
        runtime_state_directory=lambda _: tmp_path / "state",
    )
    runtime = MatCreatorRuntime(provider)
    config = AgentConfig("agent", "Native")
    await runtime.create_agent(config)
    info = await runtime.get_agent("agent")
    assert info.details["environment"]["status"] == "ready"
    assert info.details["environment"]["python"] == sys.executable
    events = [event async for event in runtime.execute(
        config, SimpleNamespace(context_id="session", run_id="run"), RuntimeInput("hello"))]
    assert events[-1].payload["text"] == "\u4f60\u597d"
    assert runtime.active == {}
