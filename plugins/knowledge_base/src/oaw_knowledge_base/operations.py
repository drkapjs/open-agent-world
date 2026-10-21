"""The operation table: one row per thing anyone can ask a knowledge base to do.

Every front door reads this table — the OAW plugin turns each row into a capability
and a resource action, the HTTP service turns it into a route, the MCP server turns it
into a tool, and the CLI turns it into a subcommand. A row with no ``tool_name`` is
deliberately unreachable by any agent on any transport: uploading raw data, converting
it in batch, managing groups, changing settings and reviewing a draft stay human acts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from . import actions


@dataclass(frozen=True)
class Operation:
    name: str
    handler: Callable[[Any, dict], dict]
    model: type[BaseModel]
    description: str
    tool_name: str | None = None

    @property
    def agent(self) -> bool:
        """Whether an agent may call this at all, on any transport."""
        return self.tool_name is not None

    def input_schema(self) -> dict:
        return self.model.model_json_schema()


OPERATIONS: tuple[Operation, ...] = (
    Operation("overview", actions.overview, actions.Overview, tool_name="knowledge_overview",
        description="Summarize this knowledge base: its groups, and source, record, schema, projection, draft, fact, entity and relation counts (optionally narrowed to one group_id), the configured markdown engines, and how many conversion jobs are running. Call this first to orient before any other knowledge tool."),
    Operation("sources", actions.sources, actions.Sources, tool_name="knowledge_sources",
        description="List uploaded sources with their markdown conversion status, record id and projection count. Use the returned source_id or record_id with knowledge_markdown and knowledge_projection_prompt."),
    Operation("markdown", actions.markdown, actions.Markdown, tool_name="knowledge_markdown",
        description="Read the markdown extracted from one source, by source_id or record_id. Long documents are windowed: use offset and limit and follow has_more to page through."),
    Operation("search", actions.search, actions.Search, tool_name="knowledge_document_search",
        description="Full-text search across every converted document's markdown. Returns ranked excerpts, each with the source filename and the heading it falls under — raw document text, not a verified fact. Use this to answer document-level questions across many sources at once instead of reading one document from the top with knowledge_markdown."),
    Operation("schemas", actions.schemas, actions.Schemas, tool_name="knowledge_schemas",
        description="List, read, create or update extraction schemas. A schema is a JSON Schema object plus the system prompt used to project markdown into structured JSON. Reuse an existing schema when one fits instead of creating a near-duplicate."),
    Operation("projection_prompt", actions.projection_prompt, actions.ProjectionPrompt,
        tool_name="knowledge_projection_prompt",
        description="Get everything needed to project one document into structured JSON: the schema's system prompt, JSON Schema definition, field descriptions and the document markdown. Produce JSON matching the definition, then submit it with knowledge_save_projection."),
    Operation("save_projection", actions.save_projection, actions.SaveProjection,
        tool_name="knowledge_save_projection",
        description="Store a structured JSON projection of one record against one schema. The result is validated and linked to the originating artifact as evidence. A projection is a candidate, not a fact: it does not enter the knowledge graph until a human approves a draft built from it."),
    Operation("projections", actions.projections, actions.Projections,
        tool_name="knowledge_projections",
        description="List stored projections, or read one in full with its evidence links. Use this to review extracted data before building a draft."),
    Operation("draft", actions.draft, actions.Draft, tool_name="knowledge_draft",
        description="List or read draft graphs, or create a new draft from selected projections. A draft is proposed knowledge awaiting human review; creating one never changes the published graph."),
    Operation("experiment_bundles", actions.experiment_bundles, actions.ExperimentBundles,
        tool_name="knowledge_experiment_bundles",
        description="List or read experiment uploads: one upload is a set of files (a photographed table, a spreadsheet, a note) describing specimens of one experiment, projected together onto one kind=\"experiment\" schema. Shows each file's conversion state and how many records the upload has produced."),
    Operation("experiment_extract_prompt", actions.experiment_extract_prompt, actions.ExperimentExtractPrompt,
        tool_name="knowledge_experiment_extract_prompt",
        description="Get everything needed to project one experiment upload onto its schema: the schema's system prompt and JSON Schema definition, the uploader's notes, and every file's markdown joined into one document (windowed: follow has_more with offset). Produce one record per specimen, then submit them with knowledge_experiment_save."),
    Operation("experiment_save", actions.experiment_save, actions.ExperimentSave,
        tool_name="knowledge_experiment_save",
        description="Store the records projected from one experiment upload as drafts, each validated against the schema and linked to the files it was read from. Pass replace_drafts=true when re-extracting so earlier drafts are replaced; published records are never touched. A draft is a candidate until a person publishes it."),
    Operation("experiments", actions.experiments, actions.Experiments,
        tool_name="knowledge_experiments",
        description="List or read experiment records — structured specimen data extracted from uploaded files and reviewed by a person. Filter by schema, upload (bundle_id) or status: a published record is approved data; a draft is still being reviewed."),
    Operation("experiment_update", actions.experiment_update, actions.ExperimentUpdate,
        tool_name="knowledge_experiment_update",
        description="Edit one draft experiment record's name, data or note, when a person asks for the change. Refuses published records. Never publishes: that is the person's click."),
    Operation("graph", actions.graph, actions.GraphQuery, tool_name="knowledge_graph",
        description="Query the published knowledge graph by entity type, relation type or name, or traverse outward from one entity. Only human-approved facts appear here."),
    Operation("jobs", actions.jobs, actions.Jobs, tool_name="knowledge_jobs",
        description="Check markdown conversion jobs and their progress events. Poll this after an upload until the job reports COMPLETED before reading its markdown."),
    # No tool_name below this line: human-only, on every transport.
    Operation("ingest", actions.ingest, actions.Ingest,
        description="Upload one raw file into a group. It stays unconverted until the Process action runs its conversion job."),
    Operation("process", actions.process_sources, actions.ProcessSources,
        description="Convert one or more uploaded sources to markdown in a batch. With no source_ids, converts every unconverted source, optionally narrowed to one group."),
    Operation("groups", actions.groups, actions.Groups,
        description="List, create, rename or delete the groups sources are filed into. Every source belongs to exactly one group; deleting a non-empty group is refused."),
    Operation("settings", actions.update_settings, actions.Settings,
        description="Read or change this knowledge base's collection name, PDF engine and MinerU base URL."),
    Operation("review", actions.review, actions.Review,
        description="Submit, reject or approve a draft. Approving publishes it as a fact revision and is the only thing that writes the knowledge graph."),
    Operation("experiment_bundle_manage", actions.experiment_bundle_manage, actions.ExperimentBundleManage,
        description="Create, rename, annotate, mark the extraction state of, or delete one experiment upload."),
    Operation("experiment_ingest", actions.experiment_ingest, actions.ExperimentIngest,
        description="Upload one raw file into an experiment upload and queue its markdown conversion at once."),
    Operation("experiment_review", actions.experiment_review, actions.ExperimentReview,
        description="Publish, unpublish or delete one experiment record. Publishing asks for confirmation and marks the record as approved data."),
    Operation("graph_schema", actions.graph_schema, actions.GraphSchema,
        description="Get or set which literature-kind schema's projections build the published knowledge graph. A person's config choice, not something an agent should redirect."),
)

BY_NAME: dict[str, Operation] = {operation.name: operation for operation in OPERATIONS}
AGENT_OPERATIONS: tuple[Operation, ...] = tuple(item for item in OPERATIONS if item.agent)

READ_ACTIONS = ("overview", "sources", "markdown", "search", "schemas", "projections",
               "experiments", "experiment_bundles", "graph", "jobs")
# The literature pipeline: project, and propose a draft graph for a person to review.
EXTRACT_ACTIONS = READ_ACTIONS + ("projection_prompt", "save_projection", "draft")
# The experiment pipeline: project an upload into records and edit drafts on request.
# Publishing is deliberately absent — it has no tool name on any transport.
EXPERIMENT_ACTIONS = READ_ACTIONS + ("experiment_extract_prompt", "experiment_save",
                                     "experiment_update")


def tool_manifest() -> list[dict]:
    """The agent-callable surface, in the shape MCP and tool-listing endpoints want."""
    return [{"name": item.tool_name, "operation": item.name, "description": item.description,
             "input_schema": item.input_schema()} for item in AGENT_OPERATIONS]
