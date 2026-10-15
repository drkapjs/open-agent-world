"""Configuration and session-scoped projection for the Pack-owned engine."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from open_agent_world.plugin_api import (
    AgentNodeBehavior, AgentNodeTemplateHandler, NodeTypeDefinition,
)


class NativeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["idle", "running", "waiting", "error"] = "idle"
    runtime_provider_id: Literal["matcreator.native"] = "matcreator.native"
    model: str = Field(default="oaw:default", title="Model")
    agent_mode: Literal["normal", "flash"] = Field(default="normal", title="Planning mode",
        description="normal uses MatCreator plan approval and DAG execution; flash delegates directly.")

    @model_validator(mode="before")
    @classmethod
    def migrate_external_runtime_paths(cls, value):
        # Persisted cards remain loadable, but can no longer select outside
        # source/interpreters. No parallel external-engine code path survives.
        if isinstance(value, dict):
            value = {key: item for key, item in value.items()
                     if key not in {"python_executable", "source_directory"}}
        return value

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
        description="The Pack's planner, execution orchestrator and recursive subagents with connected OAW tools.",
        icon="bot", color="#879a9d", deck_id="agents", deck_label="Agents", deck_icon="bot",
        default_size=(320, 210), default_status="idle", statuses=frozenset({"idle", "running", "waiting", "error"}),
        config_model=NativeConfig, traits=frozenset({"core.agent", "ui.schema-agent.v1"}),
        surfaces={"preview": True, "inspector": True, "workspace": True}, lifecycle=AgentNodeBehavior(),
        templateable=True, template_status="idle", template_handler=NativeTemplate(),
    ))
