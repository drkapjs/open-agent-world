"""Source setup and desktop exports must cover every bundled plugin's host imports."""
from importlib.metadata import version
from pathlib import Path
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def test_bundled_plugin_host_dependencies_are_declared_and_installed():
    root = Path(__file__).resolve().parents[2]
    backend = tomllib.loads((root / "backend/pyproject.toml").read_text())["project"]
    requirements = backend["dependencies"] + [requirement
        for extra in backend["optional-dependencies"].values() for requirement in extra]
    declared = {canonicalize_name(Requirement(requirement).name) for requirement in requirements}
    for manifest in sorted((root / "plugins").glob("*/pyproject.toml")):
        plugin = tomllib.loads(manifest.read_text())["project"]
        for text in plugin.get("dependencies", []):
            requirement = Requirement(text)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            assert canonicalize_name(requirement.name) in declared, f"{manifest}: missing locked host dependency {text}"
            assert version(requirement.name) in requirement.specifier, f"{manifest}: incompatible installed host dependency {text}"
