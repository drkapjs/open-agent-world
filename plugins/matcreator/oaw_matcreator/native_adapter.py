"""Tool-boundary adaptations installed only inside a private worker process.

The upstream orchestrator, graph validation, step runner, recursion, result
schemas and recovery ledger are used unchanged. Native resource tools are
replaced with the live OAW bridge, including in dynamically created children.
"""
from __future__ import annotations

import importlib
import json

BRIDGE_INSTRUCTION = """
OAW integration (applies to every instruction above):
All external resources and tools come from connected OAW cards. First call
oaw_list_tools, then oaw_invoke_tool using the returned capability_id and schema.
Use knowledge_search / knowledge_inspect for skills and knowledge, and
knowledge_save_memory for experience. Use skill_node_id for resource/script tools.
Native load_skill, query_knowledge_graph, run_python, run_bash, show_* and remote
job tools have been replaced. Do not attempt to use them. Use inspect_sandbox,
start_sandbox, execute_command and wait_sandbox_operation for computation; use
publish_artifact for completed output. All computation must run in the connected
Sandbox. The upstream workspace_dir is PRIVATE orchestration metadata, not a
Sandbox path; discover the actual Sandbox paths through inspect_sandbox.
For native validate_graph / run_*executor use suggested_skills=[] and put the
selected OAW skill IDs and necessary context in action instead.
Publish generated files with publish_artifact. Use the returned oaw_artifact_ref
in submit_step_result.artifacts, never host or Sandbox paths. A success claim
requires actual outputs and verification. A successful agent turn alone does
not establish scientific correctness. Keep OAW operation/job IDs when work is
still running, and inspect them before retrying. Never submit duplicate jobs.
Use native planner/executor/subagent tools for orchestration. Do not delegate
through OAW Summoning or a task-board scheduler. Display comes from Conversation,
MatCreator execution, Sandbox files, Published artifacts and Structure viewer.
"""

PLANNER_TOOLS = frozenset({"validate_graph", "validate_intent", "validate_summarize",
    "write_session_summary", "confirm_plan_and_start_execution", "resume_execution",
    "run_flash_step", "read_session_log"})
EXECUTOR_TOOLS = frozenset({"run_node_executor", "to_planner", "validate_summarize",
    "get_ready_nodes", "set_node_status", "mark_dependents_blocked"})
STEP_TOOLS = frozenset({"run_sub_agent", "submit_step_result"})


def read_agent_graph(path):
    """None means an unavailable read, not an authoritative empty graph.

    Upstream writes this file in place. Reading during a subagent's write must
    not publish an empty projection that erases the last complete view.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        nodes = data.get('nodes') if isinstance(data, dict) else None
        if not isinstance(nodes, dict) or any(not isinstance(node, dict) or not node.get('id') for node in nodes.values()):
            return None
        return [{key: node.get(key) for key in ("id", "type", "label", "status", "parent_id", "summary", "dependency_ids")}
                for node in nodes.values()]
    except (OSError, ValueError):
        return None


def unpack(response):
    """The process wire uses OAW's existing JSON/image representation."""
    content = response.get("result", [])
    for item in content:
        if item.get("type") == "inputText":
            return json.loads(item["text"])
    return None


def install(rpc, emit_event):
    from google.adk.tools.function_tool import FunctionTool
    from google.adk.tools.tool_context import ToolContext
    from google.adk.runners import Runner
    from google.genai import types
    entry = importlib.import_module("matcreator.agent")
    step_runner = importlib.import_module("matcreator.agents.execution_agent.step_executor_runner")
    step_module = importlib.import_module("matcreator.agents.execution_agent.step_executor")
    planning = importlib.import_module("matcreator.agents.thinking_agent.planning")

    async def oaw_list_tools() -> dict:
        """Discover currently connected OAW tools with argument schemas and target IDs."""
        return {"tools": await rpc("list_tools")}

    async def oaw_invoke_tool(capability_id: str, arguments: dict) -> dict:
        """Invoke a currently authorized OAW tool. Use exact schema from oaw_list_tools."""
        response = await rpc("invoke_tool", capability_id=capability_id, arguments=arguments)
        if not response.get("ok"):
            return {"ok": False, "error": unpack(response) or response.get("error")}
        value = unpack(response)
        if response.get("tool") == "publish_artifact" and isinstance(value, dict) and value.get("state") == "ready":
            value = {**value, "oaw_artifact_ref": "oaw:artifact:" + arguments["collection"] + ":" + value["version_id"]}
        images = []
        for item in response.get("result", []):
            if item.get("type") == "inputImage":
                import base64
                header, data = item["imageUrl"].split(",", 1)
                images.append(types.Part.from_bytes(data=base64.b64decode(data), mime_type=header[5:].split(";")[0]))
        return {"ok": True, "result": value, **({"media": images} if images else {})}

    verified = set()
    original_submit = step_module.submit_step_result

    async def submit_step_result(status: str, key_results: str, concise_summary: str,
                                 tool_context: ToolContext, artifacts: list[str] | None = None,
                                 replan_reason: str | None = None) -> dict:
        """Submit a verified step result. Artifacts must be published OAW artifact references."""
        for reference in artifacts or []:
            pieces = reference.split(":")
            if len(pieces) != 4 or pieces[:2] != ["oaw", "artifact"]:
                return {"status": "error", "message": "Publish output via OAW and use its oaw_artifact_ref."}
            definitions = await rpc("list_tools")
            tool = next((t for t in definitions if t["name"] == "inspect_artifacts"), None)
            if tool is None:
                return {"status": "error", "message": "Artifact read access is missing or revoked."}
            result = await rpc("invoke_tool", capability_id=tool["capability_id"],
                arguments={"collection": pieces[2], "version_id": pieces[3]})
            value = unpack(result)
            if not result.get("ok") or not isinstance(value, dict) or value.get("state") != "ready":
                return {"status": "error", "message": "Published artifact is unavailable or not ready."}
            verified.add(reference)
        return original_submit(status, key_results, concise_summary, tool_context, artifacts, replan_reason)

    # With postponed annotations, ADK resolves ToolContext from module globals.
    globals()["ToolContext"] = ToolContext
    bridges = [FunctionTool(oaw_list_tools), FunctionTool(oaw_invoke_tool)]

    def adapt(agent, allowed, *, child=False):
        current = {getattr(tool, "name", ""): tool for tool in agent.tools}
        missing = allowed - current.keys()
        if missing:
            raise RuntimeError("Unsupported MatCreator tool contract: " + ", ".join(sorted(missing)))
        agent.tools = [tool for tool in agent.tools if getattr(tool, "name", "") in allowed] + bridges
        if child:
            agent.tools = [FunctionTool(submit_step_result) if getattr(tool, "name", "") == "submit_step_result" else tool for tool in agent.tools]
        agent.instruction += "\n" + BRIDGE_INSTRUCTION
        return agent

    adapt(entry.orchestrator.planning_agent, PLANNER_TOOLS)
    # Waiting DAG nodes need this existing upstream state transition tool.
    entry.orchestrator.planning_agent.tools.append(FunctionTool(planning.set_node_status))
    adapt(entry.orchestrator.execution_agent, EXECUTOR_TOOLS)
    original_build = step_runner.build_step_executor_agent
    # Prevent construction/contact of configured remote A2A agents before filtering.
    step_module.load_remote_a2a_agents = lambda: []

    def build_step_executor_agent(llm_card):
        return adapt(original_build(llm_card), STEP_TOOLS, child=True)

    step_runner.build_step_executor_agent = build_step_executor_agent
    step_module.build_step_executor_agent = build_step_executor_agent
    # Input images and output files live in Sandbox/Artifact cards, not local
    # paths supplied by the model. Never dereference those paths on the host.
    step_runner._input_image_paths = lambda **kwargs: []
    step_runner._refresh_skill_registry_after_creation = lambda skills: None

    def verify_artifacts(artifacts, allowed_roots=None):
        return ([p for p in artifacts if p in verified], [p for p in artifacts if p not in verified])

    step_runner._split_verified_artifacts = verify_artifacts

    class ObservedRunner(Runner):
        async def run_async(self, **kwargs):
            from contextlib import aclosing
            async with aclosing(super().run_async(**kwargs)) as stream:
                async for event in stream:
                    emit_event(event, child=True)
                    yield event

    step_runner.Runner = ObservedRunner
    return entry.app
