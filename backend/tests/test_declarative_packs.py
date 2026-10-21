"""One canonical registration contract for source and installed declarative cards."""
from importlib.metadata import EntryPoints
import json
from pathlib import Path
import shutil
import sys

from fastapi.testclient import TestClient
import pytest

from backend.config import Settings
from backend.errors import GraphValidationError
from backend.main import create_app
from backend.packs.archive import inspect_archive
from backend.packs.build import build_project, doctor
from backend.packs.installation import PackInstallationManager
from backend.packs.spec import read_source_pack
from backend.plugins import loader
from backend.plugins.declarative import declarative_plugin
from backend.services import create_services
from backend.world.models import CardCreate, CardPatch


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(sys, 'path', list(sys.path))
    monkeypatch.setattr(loader, 'entry_points', lambda: EntryPoints())
    yield
    for name in list(sys.modules):
        if name.startswith('oaw_declarative_'):
            del sys.modules[name]


def project(tmp_path):
    root = tmp_path / 'external' / 'note'
    root.mkdir(parents=True)
    (root / 'pyproject.toml').write_text('[project]\nname="notes"\nversion="1.0.0"\ndependencies=[]\n', encoding='utf-8')
    (root / 'oaw.pack.toml').write_text('''schema_version=3
[distribution]
id="test.notes"
name="Notes"
[[contributions.cards]]
config_schema="config.json"
''', encoding='utf-8')
    (root / 'config.json').write_text(json.dumps({'type': 'object', 'properties': {
        'message': {'type': 'string', 'minLength': 1, 'maxLength': 30, 'default': 'Hello'},
        'count': {'type': 'integer', 'minimum': 1, 'maximum': 5, 'default': 1},
        'kind': {'type': 'string', 'enum': ['note', 'warning'], 'default': 'note'},
        'tags': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 3, 'default': []},
        'options': {'type': 'object', 'properties': {'pinned': {'type': 'boolean', 'default': False}}},
    }}), encoding='utf-8')
    return root


def test_static_defaults_and_normal_registration(tmp_path):
    root = project(tmp_path)
    spec = read_source_pack(root)
    assert spec.owner == spec.distribution_id == 'test.notes'
    assert spec.node_types == ('test.notes',)
    assert spec.packs == ('test.notes.default',)
    assert spec.compatibility.plugin_api == '1.37'
    assert not spec.entrypoints
    assert doctor(root)['packs'][0]['spec']['contributions']['cards']
    registry = loader.load_plugin_registry(root.parent, strict=True)
    definition = registry.node_type('test.notes')
    from open_agent_world.plugin_api import NodeTypeDefinition
    assert type(definition) is NodeTypeDefinition
    assert registry.validate_config('test.notes', {}) == {
        'message': 'Hello', 'count': 1, 'kind': 'note', 'tags': [], 'options': {'pinned': False}}
    for patch in ({'message': ''}, {'count': 9}, {'kind': 'other'}, {'tags': ['a'] * 4}, {'unexpected': 'value'}):
        with pytest.raises(GraphValidationError):
            registry.validate_config('test.notes', patch)


@pytest.mark.parametrize('schema', [
    {'type': 'object', '$ref': '#/unsafe'},
    {'type': 'object', 'properties': {'missing': {'type': 'string'}}},
    {'type': 'object', 'properties': {'bad': {'type': 'integer', 'default': 0, 'minimum': 1}}},
    {'type': 'object', 'properties': {'field': {'type': 'string', 'default': 'ok', 'minItems': 1}}},
])
def test_invalid_or_unsupported_config_schema_fails_before_activation(tmp_path, schema):
    root = project(tmp_path)
    (root / 'config.json').write_text(json.dumps(schema))
    with pytest.raises(ValueError):
        read_source_pack(root)


def test_generated_archive_uses_existing_installer_and_preserves_config_after_restart(tmp_path):
    root = project(tmp_path)
    source = loader.load_plugin_registry(root.parent, strict=True)
    artifact = build_project(root, tmp_path / 'note.oawpack')
    inspected = inspect_archive(artifact.read_bytes())
    assert inspected.manifest.schema_version == 3
    assert inspected.manifest.owner_id == 'test.notes'
    assert inspected.manifest.provided_pack_ids == ('test.notes.default',)
    settings = Settings.for_data_root(tmp_path / 'profile')
    manager = PackInstallationManager(settings.data_root, loader.create_builtin_registry())
    manager.install(artifact.read_bytes())
    registry = loader.create_builtin_registry()
    loader.load_installed_packs(registry, settings.data_root, strict=True)
    assert registry.node_type('test.notes').catalog_item('test.notes') == source.node_type('test.notes').catalog_item('test.notes')
    services = create_services(settings, plugins=registry)
    card = services.world.create_card(CardCreate(type='test.notes'))
    services.world.update_card(card.id, CardPatch(config={'message': 'Saved externally'}))
    services.close()
    registry = loader.create_builtin_registry()
    loader.load_installed_packs(registry, settings.data_root, strict=True)
    services = create_services(settings, plugins=registry)
    with TestClient(create_app(settings, services=services)) as client:
        assert client.get(f'/api/nodes/{card.id}').json()['config']['message'] == 'Saved externally'
        assert client.get('/api/packs/test.notes/versions/1.0.0/frontend/index.js').status_code == 200
    services.close()


def test_advanced_manifest_defaults_preserve_structure_viewer_contract_and_receipts(tmp_path):
    root = Path(__file__).resolve().parents[2] / 'plugins/structure_viewer'
    spec = read_source_pack(root)
    assert spec.owner == 'science.structure-viewer'
    assert spec.packs == ('science.structure-viewer.default',)
    assert spec.node_types == ('science.structure-viewer',)
    # Existing packs with no contributions keep the previous digest payload.
    import hashlib
    payload = spec.model_dump(mode='json')
    payload.pop('contributions')
    assert spec.spec_digest == hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def test_external_example_is_static_and_generates_no_python_source(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'examples/packs/notes'
    root = tmp_path / 'independent project'
    shutil.copytree(source, root)
    spec = read_source_pack(root)
    assert spec.owner == spec.node_types[0] == 'example.notes'
    assert spec.frontend.entry == 'frontend/index.tsx'
    assert not list(root.rglob('*.py'))


def test_declarative_schema_cannot_grant_capabilities_or_mix_execution_contracts(tmp_path):
    root = project(tmp_path)
    manifest = root / 'oaw.pack.toml'
    manifest.write_text(manifest.read_text() + '\ncapabilities=["sandbox.execute"]\n')
    with pytest.raises(ValueError, match='Extra inputs'):
        read_source_pack(root)


def test_all_ambiguous_sources_stay_unavailable_even_with_three_claimants(tmp_path):
    root = project(tmp_path)
    shutil.copytree(root, root.parent / 'second')
    shutil.copytree(root, root.parent / 'third')
    registry = loader.load_plugin_registry(root.parent)
    assert not registry.has_plugin('test.notes')
    assert len(registry.load_failures) == 3
    assert all(item['phase'] == 'discovery' for item in registry.load_failures)
