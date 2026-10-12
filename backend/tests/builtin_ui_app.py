"""Real HTTP visual fixtures with an offline MatCreator Sandbox.

No scientific process is started by this suite. Keep the production preset's
network intent intact and make the isolated browser profile portable to hosts
without the optional Windows network-policy broker.
"""
from backend.config import Settings
from backend.main import create_app
from backend.services import create_services

settings = Settings.from_environment()
services = create_services(settings)
preset = services.plugins._legion_presets["matcreator.research"]
services.plugins._legion_presets[preset.id] = preset.model_copy(update={
    "nodes": tuple(node.model_copy(update={"config": {**node.config, "network_enabled": False}})
                   if node.type == "sandbox" else node for node in preset.nodes),
})
app = create_app(settings, services=services)
