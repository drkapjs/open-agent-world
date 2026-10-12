"""The standard installer owns trusted Pack preparation, including desktop builds."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.fixture
def installer(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / "scripts" / "install-plugins.py"
    spec = importlib.util.spec_from_file_location("plugin_installer", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin = tmp_path / "example"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text('''[project]
name = "example-pack"
[project.entry-points."open_agent_world.plugins"]
example = "example:create_plugin"
[project.entry-points."open_agent_world.setup"]
native = "example:prepare"
''')
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda args, **kwargs: calls.append(args))
    return module, plugin, calls


@pytest.mark.parametrize("mode", ["editable", "target", "dry-run"])
def test_plugin_hooks_follow_successful_install_in_destination_python(installer, monkeypatch, tmp_path, mode):
    module, plugin, calls = installer
    args = ["install-plugins.py", str(plugin), "--python", "destination-python"]
    if mode == "target":
        args += ["--target", str(tmp_path / "payload")]
    if mode == "dry-run":
        args += ["--dry-run"]
    monkeypatch.setattr(sys, "argv", args)
    module.main()
    assert calls[0][:5] == ["uv", "pip", "install", "--python", "destination-python"]
    assert len(calls) == (1 if mode == "dry-run" else 2)
    if mode != "dry-run":
        assert calls[1][:2] == ["destination-python", "-c"]
        assert calls[1][-2:] == ["example-pack", str(tmp_path / "payload") if mode == "target" else ""]


def test_failed_dependency_install_never_runs_hooks(installer, monkeypatch):
    module, plugin, calls = installer

    def fail(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(module.subprocess, "run", fail)
    monkeypatch.setattr(sys, "argv", ["install-plugins.py", str(plugin)])
    with pytest.raises(subprocess.CalledProcessError):
        module.main()
    assert len(calls) == 1
