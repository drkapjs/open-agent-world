"""Root entry point for MatCreator.

Wires the PlanningExecutionOrchestrator into a resumable ADK App.
OAW's request-boundary policy compacts each model context independently.

Routing (per user invocation):
  1. Planning   — thinking_agent understands the goal, builds a plan, asks confirmation.
  2. Execution  — execution_agent runs each plan step (one step per sub-invocation).
"""

from google.adk.apps.app import App
from google.adk.apps import ResumabilityConfig

from .agents.thinking_agent import thinking_agent
from .agents.execution_agent import execution_agent
from .agents.orchestrator.agent import PlanningExecutionOrchestrator
from .adk_compat import install_lenient_tool_argument_parsing

# Must run before any LiteLlm stream finalizes a tool call: repair tool-call
# argument JSON with unescaped inner quotes instead of crashing the stream.
install_lenient_tool_argument_parsing()

orchestrator = PlanningExecutionOrchestrator(
    name="MatCreator",
    planning_agent=thinking_agent,
    execution_agent=execution_agent,
)

app = App(
    name="MatCreator",
    root_agent=orchestrator,
    resumability_config=ResumabilityConfig(
        is_resumable=True,
    ),
)
