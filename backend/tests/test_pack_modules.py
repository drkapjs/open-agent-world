"""Schema 3 adapters have verified filesystem resources, including workers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from backend.packs.archive import inspect_archive
from backend.packs.installation import PackInstallationManager
from backend.packs.modules import materialize_modules
from backend.plugins.builtin import create_builtin_registry
from backend.plugins.loader import load_installed_packs


def zipped(files):
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def distribution():
    prefix = "oaw_module_fixture-1.0.0.dist-info"
    wheel = zipped({
        "oaw_module_fixture/__init__.py": '''
from pathlib import Path
from open_agent_world.plugin_api import PluginDescriptor, PluginDefinition, PackDefinition, NodeTypeDefinition
from pydantic import BaseModel
class Config(BaseModel):
    pass
RESOURCE = Path(__file__).with_name("data.txt").read_text()
WORKER = Path(__file__).with_name("worker.py")
def register(registration):
    registration.register_node_type(NodeTypeDefinition(id="module.fixture.card", label="Fixture", description="Test", icon="boxes", color="#123456", deck_id="test", deck_label="Test", deck_icon="boxes", default_name="Fixture", default_size=(300,200), default_status="idle", statuses=frozenset({"idle"}), config_model=Config))
    registration.register_pack(PackDefinition(id="module.fixture.default", name="Fixture", cards=("module.fixture.card",)))
def create_plugin():
    return PluginDefinition(PluginDescriptor(id="module.fixture", version="1.0.0", plugin_api_version="1.23"), register)
''',
        "oaw_module_fixture/data.txt": "canonical resource",
        "oaw_module_fixture/worker.py": '''
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from oaw_module_fixture_engine import result
print(result())
''',
        "oaw_module_fixture_engine/__init__.py": '''
from pathlib import Path
def result():
    return Path(__file__).with_name("engine.txt").read_text()
''',
        "oaw_module_fixture_engine/engine.txt": "isolated engine resource",
        f"{prefix}/METADATA": "Metadata-Version: 2.1\nName: oaw-module-fixture\nVersion: 1.0.0\n",
        f"{prefix}/WHEEL": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{prefix}/entry_points.txt": "[open_agent_world.plugins]\nmodule.fixture=oaw_module_fixture:create_plugin\n",
    })
    manifest = {
        "schema_version": 3, "id": "module.fixture.release", "owner": "module.fixture",
        "packs": ["module.fixture.default"], "name": "Module Fixture", "version": "1.0.0",
        "backend_modules": ["oaw_module_fixture", "oaw_module_fixture_engine"],
        "compatibility": {"oaw": ">=0.1.0,<1", "plugin_api": "1.23", "frontend_api": 1},
        "entrypoints": {"backend": "backend/fixture.whl", "frontend": "frontend/index.js"},
    }
    files = {"manifest.json": json.dumps(manifest).encode(), "backend/fixture.whl": wheel,
             "frontend/index.js": b"export default {apiVersion:1,views:{}}"}
    files["checksums.json"] = json.dumps({key: hashlib.sha256(value).hexdigest() for key, value in files.items()}).encode()
    return zipped(files)


@pytest.fixture(autouse=True)
def isolate_imports(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    for name in list(sys.modules):
        if name.startswith("oaw_module_fixture"):
            del sys.modules[name]


def materialized(tmp_path):
    archive = inspect_archive(distribution())
    wheel = archive.files[archive.manifest.entrypoints.backend]
    digest = hashlib.sha256(wheel).hexdigest()
    cache = tmp_path / "cache" / "modules"
    root = materialize_modules(wheel, archive.manifest, cache, expected_digest=digest)
    return root, wheel, archive.manifest, cache, digest


def test_inspection_has_no_materialization_or_import_side_effects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inspected = inspect_archive(distribution())
    assert inspected.manifest.owner_id == "module.fixture"
    assert list(tmp_path.iterdir()) == []
    assert "oaw_module_fixture" not in sys.modules


def test_installed_schema3_has_real_file_resources_and_isolated_worker(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    manager.install(distribution())
    registry = create_builtin_registry()
    load_installed_packs(registry, tmp_path)
    module = sys.modules["oaw_module_fixture"]
    assert Path(module.__file__).is_file()
    assert module.RESOURCE == "canonical resource"
    assert module.WORKER.is_file()
    assert "cache" in Path(module.__file__).parts
    assert ".whl" not in module.__file__
    result = subprocess.run([sys.executable, "-I", "-S", str(module.WORKER)], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "isolated engine resource"
    # Another app reuses the same origin and discards worker-generated bytecode.
    original = module.__file__
    load_installed_packs(create_builtin_registry(), tmp_path)
    assert sys.modules["oaw_module_fixture"].__file__ == original
    assert not list(Path(original).parent.parent.rglob("*.pyc"))


@pytest.mark.parametrize("mutation", ["changed", "missing", "extra", "directory"])
def test_materialized_tree_tampering_is_rejected(tmp_path, mutation):
    root, wheel, manifest, cache, digest = materialized(tmp_path)
    resource = root / "oaw_module_fixture" / "data.txt"
    if mutation == "changed":
        resource.write_text("tampered", encoding="utf-8")
    elif mutation == "missing":
        resource.unlink()
    elif mutation == "extra":
        (root / "oaw_module_fixture" / "injected.py").write_text("unexpected", encoding="utf-8")
    else:
        (root / "unexpected").mkdir()
    with pytest.raises(ValueError, match="checksum mismatch|missing files|Unexpected materialized"):
        materialize_modules(wheel, manifest, cache, expected_digest=digest)


def test_generated_bytecode_is_removed_but_other_cache_contents_are_rejected(tmp_path):
    root, wheel, manifest, cache, digest = materialized(tmp_path)
    bytecode = root / "oaw_module_fixture" / "__pycache__"
    bytecode.mkdir()
    (bytecode / "__init__.malicious.pyc").write_bytes(b"never trust bytecode")
    assert materialize_modules(wheel, manifest, cache, expected_digest=digest) == root
    assert not bytecode.exists()
    bytecode.mkdir()
    (bytecode / "surprise.py").write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected materialized Pack file"):
        materialize_modules(wheel, manifest, cache, expected_digest=digest)


def test_installed_cache_tampering_blocks_loading_even_if_module_is_already_loaded(tmp_path):
    manager = PackInstallationManager(tmp_path, create_builtin_registry())
    manager.install(distribution())
    load_installed_packs(create_builtin_registry(), tmp_path)
    source = Path(sys.modules["oaw_module_fixture"].__file__)
    source.write_text("raise RuntimeError('unverified code')", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Materialized Pack checksum mismatch"):
        load_installed_packs(create_builtin_registry(), tmp_path)


def test_canonical_wheel_checksum_is_rechecked_before_any_cache_write(tmp_path):
    archive = inspect_archive(distribution())
    wheel = archive.files[archive.manifest.entrypoints.backend]
    cache = tmp_path / "cache"
    with pytest.raises(ValueError, match="wheel checksum changed"):
        materialize_modules(wheel, archive.manifest, cache, expected_digest="0" * 64)
    assert not cache.exists()


def test_concurrent_materialization_publishes_one_complete_tree(tmp_path):
    archive = inspect_archive(distribution())
    wheel = archive.files[archive.manifest.entrypoints.backend]
    digest = hashlib.sha256(wheel).hexdigest()
    cache = tmp_path / "cache"
    with ThreadPoolExecutor(max_workers=4) as executor:
        roots = list(executor.map(lambda _: materialize_modules(wheel, archive.manifest, cache, expected_digest=digest), range(4)))
    assert len(set(roots)) == 1
    assert (roots[0] / "oaw_module_fixture_engine" / "engine.txt").read_text() == "isolated engine resource"
    assert not list(cache.glob(".staging-*"))


def test_schema1_continues_to_use_existing_zip_contract(tmp_path):
    archive = inspect_archive(distribution())
    with pytest.raises(ValueError, match="Only schema 3"):
        materialize_modules(archive.files[archive.manifest.entrypoints.backend],
                            archive.manifest.model_copy(update={"schema_version": 1}), tmp_path, expected_digest="0" * 64)

