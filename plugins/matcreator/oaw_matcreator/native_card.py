"""Configuration and session-scoped projection for the upstream runtime."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from open_agent_world.plugin_api import (
    AgentNodeBehavior, AgentNodeTemplateHandler, NodeTypeDefinition,
)

UPSTREAM_REVISION = "0328c8b814499cbf3e49116323cee050145fa08f"


class NativeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["idle", "running", "waiting", "error"] = "idle"
    runtime_provider_id: Literal["matcreator.native"] = "matcreator.native"
    model: str = Field(default="oaw:default", title="Model")
    agent_mode: Literal["normal", "flash"] = Field(default="normal", title="Planning mode",
        description="normal uses MatCreator plan approval and DAG execution; flash delegates directly.")
    python_executable: str = Field(default="", title="MatCreator Python override (optional)",
        description="Leave blank to use the runtime managed by the plugin. For development only: absolute Python 3.12+ path with the pinned runtime installed.",
        json_schema_extra={"privileged": True})
    source_directory: str = Field(default="", title="MatCreator checkout",
        description="Optional absolute path to the supported devel checkout. Blank uses the installed package.",
        json_schema_extra={"privileged": True})
    system_instruction: str = Field(default="", title="Additional instructions", json_schema_extra={"format": "textarea"})
    max_concurrent_runs: Literal[1] = 1
    inherit_legion_model: bool = True


class NativeTemplate(AgentNodeTemplateHandler):
    portable_config_fields = AgentNodeTemplateHandler.portable_config_fields | frozenset({"agent_mode", "inherit_legion_model"})


def register(registration):
    from .native_runtime import MatCreatorRuntime
    registration.register_runtime_provider("matcreator.native", MatCreatorRuntime)
    registration.register_node_type(NodeTypeDefinition(
        id="matcreator.agent", label="MatCreator Agent", default_name="MatCreator Agent",
        description="MatCreator's native planner, execution orchestrator and recursive subagents with connected OAW tools.",
        icon="bot", color="#879a9d", deck_id="agents", deck_label="Agents", deck_icon="bot",
        default_size=(320, 210), default_status="idle", statuses=frozenset({"idle", "running", "waiting", "error"}),
        config_model=NativeConfig, traits=frozenset({"core.agent", "ui.schema-agent.v1"}),
        surfaces={"preview": True, "inspector": True, "workspace": True}, lifecycle=AgentNodeBehavior(),
        templateable=True, template_status="idle", template_handler=NativeTemplate(),
    ))
