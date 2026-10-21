"""End-to-end pipeline: upload, convert, project, review, graph — with no host at all.

The suite drives the same handlers the OAW card and the service call, through the
plain :class:`~oaw_knowledge_base.context.KnowledgeContext`, so it runs in any venv
that has ``mat-know-base`` installed. ``cd plugins/knowledge_base && pytest``.
"""
import base64
import json
import uuid

import pytest

pytest.importorskip("mkb", reason="Install mat-know-base into this environment")
pytest.importorskip("sqlalchemy")

from oaw_knowledge_base import actions, client  # noqa: E402
from oaw_knowledge_base.context import KnowledgeContext, MemorySettings  # noqa: E402
from oaw_knowledge_base.errors import KnowledgeError  # noqa: E402

SCHEMA = {
    "type": "object",
    "required": ["entities"],
    "properties": {
        "entities": {"type": "array", "items": {"type": "object"}},
        "relations": {"type": "array", "items": {"type": "object"}},
    },
}
DOCUMENT = """# Sintering of Si3N4

## Method
The Si3N4 powder was sintered at 1750 C for 2 hours under nitrogen.

| Sample | Density |
|---|---|
| A1 | 3.21 |
"""


@pytest.fixture
def card(tmp_path):
    storage = tmp_path / "node-1"
    storage.mkdir()
    yield KnowledgeContext("node-1", storage, state=MemorySettings())
    client.close_client("node-1")


def run(context, handler, **arguments):
    return handler(context, arguments)


def ingest_document(context, text=DOCUMENT, filename="sintering.md"):
    ingested = run(context, actions.ingest, filename=filename,
                   content_base64=base64.b64encode(text.encode()).decode(),
                   media_type="text/markdown")
    source_id = ingested["source"]["id"]
    processed = run(context, actions.process_sources, source_ids=[source_id])
    kb = client.open_client(context.node_id, context.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return source_id, job


def test_ingest_converts_to_markdown_with_evidence(card):
    source_id, _ = ingest_document(card)

    listing = run(card, actions.sources)["sources"]
    assert len(listing) == 1
    assert listing[0]["markdown"]["engine"] == "text"
    record_id = listing[0]["record_id"]
    assert record_id

    document = run(card, actions.markdown, source_id=source_id)
    assert "Sintering of Si3N4" in document["markdown"]
    assert document["has_more"] is False

    # The markdown record must point back at the uploaded file (traceability).
    kb = client.open_client(card.node_id, card.storage_path)
    links = kb.evidence.list(output_id=record_id)
    assert [str(link.source_id) for link in links] == [source_id]


def test_mineru_job_resolves_a_private_token_without_persisting_it(card, monkeypatch):
    from oaw_knowledge_base import markdown

    seen = []
    card.resolve_secret = lambda reference: "private-job-token" if reference == "OAW_MINERU_TOKEN" else None
    def convert(data, filename, base_url, **kwargs):
        seen.append(kwargs["token"])
        return "# Converted paper"
    monkeypatch.setattr(markdown, "_mineru_markdown", convert)
    run(card, actions.update_settings, pdf_engine="mineru", mineru_base_url="https://mineru.example")
    source = run(card, actions.ingest, filename="paper.pdf", media_type="application/pdf",
                 content_base64=base64.b64encode(b"%PDF-test").decode())["source"]
    result = run(card, actions.process_sources, source_ids=[source["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(result["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    assert seen == ["private-job-token"]
    assert "private-job-token" not in json.dumps(card.state.get())
    from sqlalchemy import text
    with kb.oaw_engine.connect() as connection:
        tables = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        for (table,) in list(tables):
            escaped = table.replace('"', '""')
            rows = connection.execute(text(f'SELECT * FROM "{escaped}"')).fetchall()
            assert "private-job-token" not in str(rows), table


def test_conversion_also_indexes_the_document_for_search(card):
    source_id, _ = ingest_document(card)
    kb = client.open_client(card.node_id, card.storage_path)

    from oaw_knowledge_base import search_store

    hits = search_store.search(kb.oaw_engine, "sintered")
    assert hits and hits[0]["source_id"] == source_id
    assert hits[0]["group_id"] == str(kb.collections.list()[0].id)


def test_reopening_the_client_backfills_records_indexed_before_search_existed(card):
    source_id, _ = ingest_document(card)
    kb = client.open_client(card.node_id, card.storage_path)

    from oaw_knowledge_base import search_store

    # Simulate a database from before the search feature existed: its chunks are
    # gone, as if this record's conversion predated search_store entirely.
    with kb.oaw_engine.begin() as connection:
        connection.exec_driver_sql(f"DELETE FROM {search_store.TABLE}")
    assert search_store.search(kb.oaw_engine, "sintered") == []

    client.close_client(card.node_id)
    reopened = client.open_client(card.node_id, card.storage_path)
    hits = search_store.search(reopened.oaw_engine, "sintered")
    assert hits and hits[0]["source_id"] == source_id


def test_search_action_returns_attributable_excerpts_scoped_to_a_group(card):
    source_id, _ = ingest_document(card, filename="sintering.md")
    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]
    ingested = run(card, actions.ingest, filename="alloy.md",
                   content_base64=base64.b64encode(
                       b"# Alloy notes\n\nA different microstructure entirely.\n").decode(),
                   media_type="text/markdown", group_id=alloys["id"])
    processed = run(card, actions.process_sources, source_ids=[ingested["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error

    found = run(card, actions.search, query="sintered")["results"]
    assert found and found[0]["source_id"] == source_id
    assert found[0]["filename"] == "sintering.md"
    assert found[0]["group_name"] == "Knowledge base"
    assert "Method" in (found[0]["heading_path"] or "")

    # Scoped to a group with nothing matching: no results, even though the term
    # exists elsewhere in the base.
    assert run(card, actions.search, query="sintered", group_id=alloys["id"])["results"] == []

    # A no-op query (all whitespace or nothing meaningful) returns cleanly, not an error.
    assert run(card, actions.search, query="   ")["results"] == []


def test_conversion_job_is_idempotent(card):
    source_id, first = ingest_document(card)
    kb = client.open_client(card.node_id, card.storage_path)
    source = kb.sources.require(source_id)
    from oaw_knowledge_base.pipelines import submit_markdown_job

    second = submit_markdown_job(kb, source, kb.collections.list()[0].id, engine="auto")
    assert str(second.id) == str(first.id)
    assert len([a for a in kb.artifacts.list(source_id=source_id)
                if a.processing_type == "MARKDOWN"]) == 1


def test_projection_requires_a_schema_shaped_definition(card):
    with pytest.raises(KnowledgeError):
        run(card, actions.schemas, operation="create", name="bad",
            definition={"type": "string"}, system_prompt="x")


def test_schemas_default_to_literature_and_can_be_tagged_and_filtered(card):
    literature = run(card, actions.schemas, operation="create", name="Process graph",
                     definition=SCHEMA, system_prompt="x")["schema"]
    assert literature["kind"] == "literature"

    experiment = run(card, actions.schemas, operation="create", name="Synthesis run",
                     definition=SCHEMA, system_prompt="x", kind="experiment")["schema"]
    assert experiment["kind"] == "experiment"

    # A schema created before "kind" existed carries MKB's own "freeform" purpose;
    # that must still read back as literature, with no migration needed.
    kb = client.open_client(card.node_id, card.storage_path)
    legacy = kb.schemas.create(name="Legacy", domain="materials", definition=SCHEMA,
                               system_prompt="x")
    assert legacy.purpose == "freeform"
    legacy_json = run(card, actions.schemas, operation="get", schema_id=str(legacy.id))["schema"]
    assert legacy_json["kind"] == "literature"

    all_names = {item["name"] for item in run(card, actions.schemas)["schemas"]}
    assert all_names == {"Process graph", "Synthesis run", "Legacy"}
    experiment_only = run(card, actions.schemas, kind="experiment")["schemas"]
    assert [item["name"] for item in experiment_only] == ["Synthesis run"]

    retagged = run(card, actions.schemas, operation="update", schema_id=experiment["id"],
                   kind="literature")["schema"]
    assert retagged["kind"] == "literature"


def test_graph_schema_is_one_stored_pointer_restricted_to_literature_schemas(card):
    # Unset until someone chooses one; "get" alone never fails.
    assert run(card, actions.graph_schema)["schema_id"] == ""
    assert run(card, actions.overview)["graph_schema_id"] is None

    literature = run(card, actions.schemas, operation="create", name="Process graph",
                     definition=SCHEMA, system_prompt="x")["schema"]
    experiment = run(card, actions.schemas, operation="create", name="Synthesis run",
                     definition=SCHEMA, system_prompt="x", kind="experiment")["schema"]

    with pytest.raises(KnowledgeError):
        run(card, actions.graph_schema, operation="set", schema_id=experiment["id"])

    set_result = run(card, actions.graph_schema, operation="set", schema_id=literature["id"])
    assert set_result["schema_id"] == literature["id"]
    assert run(card, actions.graph_schema)["schema_id"] == literature["id"]
    assert run(card, actions.overview)["graph_schema_id"] == literature["id"]

    # Setting it again to a blank id clears it.
    cleared = run(card, actions.graph_schema, operation="set", schema_id="")
    assert cleared["schema_id"] == ""
    assert run(card, actions.overview)["graph_schema_id"] is None


def test_full_loop_publishes_only_approved_facts(card):
    source_id, _ = ingest_document(card)
    record_id = run(card, actions.sources)["sources"][0]["record_id"]

    schema = run(card, actions.schemas, operation="create", name="Process graph",
                 definition=SCHEMA, system_prompt="Extract entities and relations.",
                 description="Samples and the processes applied to them")["schema"]

    prompt = run(card, actions.projection_prompt, schema_id=schema["id"], source_id=source_id)
    assert prompt["system_prompt"] == "Extract entities and relations."
    assert "Si3N4" in prompt["markdown"]
    assert prompt["record_id"] == record_id
    assert prompt["artifact_id"]

    extracted = {
        "entities": [
            {"type": "material", "name": "Si3N4", "form": "powder"},
            {"type": "process", "name": "Sintering", "temperature_c": 1750},
        ],
        "relations": [{"source": "Si3N4", "target": "Sintering", "type": "processed_by"}],
    }
    saved = run(card, actions.save_projection, schema_id=schema["id"], record_id=record_id,
                data=extracted, model="test-model")["projection"]
    assert saved["validation"]["valid"] is True

    stored = run(card, actions.projections, projection_id=saved["id"])["projection"]
    assert stored["data"] == extracted
    assert stored["evidence"] and stored["evidence"][0]["source_id"] == source_id

    # Saving a projection copies the record's evidence, so the source listing must still
    # resolve the record itself rather than the projection that cites the same artifact.
    assert run(card, actions.sources)["sources"][0]["record_id"] == record_id

    built = run(card, actions.draft, operation="create", projection_ids=[saved["id"]])["draft"]
    assert built["entities"] == 2 and built["relations"] == 1

    # An unapproved draft must not reach the graph.
    assert run(card, actions.graph)["entities"] == []

    run(card, actions.review, operation="submit", draft_id=built["id"], expected_revision=1)
    pending = run(card, actions.review, operation="approve", draft_id=built["id"],
                  expected_revision=1)
    assert pending["status"] == "confirmation_required"
    assert run(card, actions.graph)["entities"] == []

    confirmed = KnowledgeContext(card.node_id, card.storage_path, state=card.state,
                                 confirmed=True)
    published = run(confirmed, actions.review, operation="approve", draft_id=built["id"],
                    expected_revision=1)
    assert published["decision"] == "APPROVED"
    assert published["event"]["type"] == "fact.revision.published"
    assert published["graph"] == {"entities": 2, "relations": 1}

    result = run(card, actions.graph)
    assert sorted(item["name"] for item in result["entities"]) == ["Si3N4", "Sintering"]
    assert result["relations"][0]["type"] == "processed_by"

    material = next(item for item in result["entities"] if item["name"] == "Si3N4")
    assert material["properties"]["form"] == "powder"
    neighbours = run(card, actions.graph, operation="traverse", entity_id=material["id"])
    assert {item["name"] for item in neighbours["entities"]} == {"Si3N4", "Sintering"}

    summary = run(card, actions.overview)
    assert summary["counts"]["facts"] == 1
    assert summary["counts"]["entities"] == 2
    assert summary["counts"]["pending_review"] == 0


def test_graph_survives_a_client_restart(card):
    kb = client.open_client(card.node_id, card.storage_path)
    group = client.collection(kb)
    kb.graph.extract({"entities": [{"type": "material", "name": "Cu"}]},
                     extractor=lambda payload: payload)
    assert len(kb.graph.query().entities) == 1

    client.close_client(card.node_id)
    reopened = client.open_client(card.node_id, card.storage_path)
    assert [entity.name for entity in reopened.graph.query().entities] == ["Cu"]
    assert str(reopened.collections.list()[0].id) == str(group.id)


def test_relations_need_both_endpoints(card):
    import uuid

    from mkb.exceptions import ConflictError

    kb = client.open_client(card.node_id, card.storage_path)
    entity = kb.graph.upsert_entity(type="material", name="Fe")

    with pytest.raises(ConflictError):
        kb.oaw_graph_store.upsert_relation(
            type("R", (), {"id": uuid.uuid4(), "source_id": entity.id,
                           "target_id": uuid.uuid4(), "type": "x", "properties": {},
                           "created_at": entity.created_at, "updated_at": entity.updated_at})())


def test_settings_drive_the_collection_and_engine(card):
    updated = run(card, actions.update_settings, collection_name="Alloys", pdf_engine="text")
    assert updated["settings"]["collection_name"] == "Alloys"

    summary = run(card, actions.overview)
    assert summary["collection"]["name"] == "Alloys"
    assert summary["settings"]["pdf_engine"] == "text"
    assert summary["settings"]["vision_model"] == ""
    assert "text" in summary["engines"]

    # The vision model is a stored reference the conversion job resolves later.
    updated = run(card, actions.update_settings, vision_model="oaw:model:m1")
    assert updated["settings"]["vision_model"] == "oaw:model:m1"
    assert updated["settings"]["collection_name"] == "Alloys"


def test_uploads_are_bounded_and_validated(card):
    # Handlers raise KnowledgeError for their own rules and pydantic's ValidationError
    # for the request shape; both are ValueErrors, which is what every caller sees.
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="../escape.md", content_base64="aGk=")
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="x.md", content_base64="not base64!!")
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="empty.md", content_base64="")
    with pytest.raises(ValueError):
        run(card, actions.ingest, filename="x.md", content_base64="aGk=", sql="DROP")


def test_an_unconvertible_upload_fails_in_the_job_not_the_action(card):
    # Ingest must stay fast and never convert: the mutation lock is held for its duration.
    submitted = run(card, actions.ingest, filename="scan.bin",
                    content_base64=base64.b64encode(b"\x00\x01\x02").decode(),
                    media_type="application/octet-stream")
    assert "job" not in submitted
    processed = run(card, actions.process_sources, source_ids=[submitted["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "FAILED"
    assert "markdown engine" in (job.error or "")

    listing = run(card, actions.sources)["sources"]
    assert listing[0]["markdown"] is None
    with pytest.raises(KnowledgeError):
        run(card, actions.markdown, source_id=submitted["source"]["id"])


def test_an_image_upload_converts_through_a_configured_vision_engine(card, monkeypatch):
    class _FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {
                "content": "Handwritten note: sample A1 fired at 1750 C."}}]}

    class _FakeClient:
        def __init__(self, timeout=None):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            return _FakeResponse()

    monkeypatch.setenv("OAW_VISION_API_KEY", "test-key")
    monkeypatch.setenv("OAW_VISION_BASE_URL", "https://vision.example/v1")
    monkeypatch.setattr("httpx.Client", _FakeClient)

    submitted = run(card, actions.ingest, filename="notebook.jpg",
                    content_base64=base64.b64encode(b"fake-jpeg-bytes").decode(),
                    media_type="image/jpeg")
    processed = run(card, actions.process_sources, source_ids=[submitted["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error

    document = run(card, actions.markdown, source_id=submitted["source"]["id"])
    assert document["engine"] == "vision"
    assert "1750 C" in document["markdown"]

    # The vision transcription is searchable exactly like any other converted source.
    from oaw_knowledge_base import search_store

    hits = search_store.search(kb.oaw_engine, "handwritten note sample")
    assert hits and hits[0]["source_id"] == submitted["source"]["id"]


def test_jobs_report_progress_events(card):
    source_id, job = ingest_document(card)
    detail = run(card, actions.jobs, job_id=str(job.id))
    assert detail["job"]["status"] == "COMPLETED"
    assert detail["job"]["source_id"] == source_id
    assert any("markdown" in (event["message"] or "").lower() for event in detail["events"])
    assert json.dumps(detail)  # the payload must stay JSON serializable


def test_groups_can_be_created_renamed_and_deleted(card):
    listed = run(card, actions.groups)["groups"]
    assert len(listed) == 1 and listed[0]["is_default"]
    default_id = listed[0]["id"]

    created = run(card, actions.groups, operation="create", name="Alloys")["group"]
    assert created["name"] == "Alloys" and created["source_count"] == 0

    with pytest.raises(KnowledgeError):
        run(card, actions.groups, operation="create", name="Alloys")

    renamed = run(card, actions.groups, operation="rename",
                  group_id=created["id"], name="Alloys v2")["group"]
    assert renamed["name"] == "Alloys v2"

    run(card, actions.groups, operation="delete", group_id=created["id"])
    assert {item["id"] for item in run(card, actions.groups)["groups"]} == {default_id}

    with pytest.raises(KnowledgeError):
        run(card, actions.groups, operation="delete", group_id=default_id)


def test_sources_are_filed_into_the_chosen_group_and_listed_separately(card):
    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]

    default_source, _ = ingest_document(card, filename="default.md")
    ingested = run(card, actions.ingest, filename="alloy.md",
                  content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
                  media_type="text/markdown", group_id=alloys["id"])
    assert ingested["group_id"] == alloys["id"]
    assert "job" not in ingested  # a batch of one still needs an explicit process step

    # The default view stays scoped to the default group, exactly as a plain card
    # without any groups behaves.
    default_only = run(card, actions.sources)["sources"]
    assert [item["filename"] for item in default_only] == ["default.md"]

    scoped = run(card, actions.sources, group_id=alloys["id"])["sources"]
    assert [item["filename"] for item in scoped] == ["alloy.md"]
    assert scoped[0]["group_name"] == "Alloys"

    everything = run(card, actions.sources, all_groups=True)["sources"]
    assert {item["filename"] for item in everything} == {"default.md", "alloy.md"}

    overview_default = run(card, actions.overview)
    assert overview_default["counts"]["sources"] == 1
    assert {group["name"] for group in overview_default["groups"]} == {"Knowledge base", "Alloys"}
    overview_all = run(card, actions.overview, all_groups=True)
    assert overview_all["counts"]["sources"] == 2


def test_process_sources_runs_in_batch_and_skips_what_is_already_converted(card):
    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]
    ids = []
    for name in ("a.md", "b.md"):
        result = run(card, actions.ingest, filename=name,
                     content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
                     media_type="text/markdown", group_id=alloys["id"])
        ids.append(result["source"]["id"])
        assert "job" not in result  # uploading never queues its own conversion

    processed = run(card, actions.process_sources, source_ids=ids)
    kb = client.open_client(card.node_id, card.storage_path)
    for entry in processed["jobs"]:
        kb.jobs.wait(entry["job"]["id"], timeout=60)
    listing = run(card, actions.sources, group_id=alloys["id"])["sources"]
    assert all(item["markdown"] is not None for item in listing)

    # Re-running the batch over already-converted sources is a safe no-op.
    again = run(card, actions.process_sources, source_ids=ids)
    assert all(entry.get("skipped") == "already converted" for entry in again["jobs"])

    # With no source_ids, "process all pending" only picks up unconverted sources.
    run(card, actions.ingest, filename="c.md",
        content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
        media_type="text/markdown", group_id=alloys["id"])
    pending = run(card, actions.process_sources, group_id=alloys["id"])
    assert len(pending["jobs"]) == 1 and pending["jobs"][0]["filename"] == "c.md"


def test_drafts_and_the_graph_stay_scoped_to_the_group_that_built_them(card):
    default_source, _ = ingest_document(card, filename="default.md")
    default_record = run(card, actions.sources)["sources"][0]["record_id"]

    alloys = run(card, actions.groups, operation="create", name="Alloys")["group"]
    result = run(card, actions.ingest, filename="alloy.md",
                content_base64=base64.b64encode(DOCUMENT.encode()).decode(),
                media_type="text/markdown", group_id=alloys["id"])
    processed = run(card, actions.process_sources, source_ids=[result["source"]["id"]])
    kb = client.open_client(card.node_id, card.storage_path)
    kb.jobs.wait(processed["jobs"][0]["job"]["id"], timeout=60)
    alloy_record = run(card, actions.sources, group_id=alloys["id"])["sources"][0]["record_id"]

    schema = run(card, actions.schemas, operation="create", name="Process graph",
                definition=SCHEMA, system_prompt="Extract entities and relations.")["schema"]

    def project(record_id, name):
        data = {"entities": [{"type": "material", "name": name}], "relations": []}
        return run(card, actions.save_projection, schema_id=schema["id"],
                  record_id=record_id, data=data)["projection"]["id"]

    default_projection = project(default_record, "Cu")
    alloy_projection = project(alloy_record, "Steel")

    # Mixing groups in one draft is refused, so a published graph traces back to
    # exactly one group's sources.
    with pytest.raises(KnowledgeError):
        run(card, actions.draft, operation="create",
            projection_ids=[default_projection, alloy_projection])

    default_draft = run(card, actions.draft, operation="create",
                        projection_ids=[default_projection])["draft"]
    alloy_draft = run(card, actions.draft, operation="create",
                      projection_ids=[alloy_projection])["draft"]

    confirmed = KnowledgeContext(card.node_id, card.storage_path, state=card.state,
                                confirmed=True)
    for built in (default_draft, alloy_draft):
        run(card, actions.review, operation="submit", draft_id=built["id"], expected_revision=1)
        run(confirmed, actions.review, operation="approve", draft_id=built["id"],
            expected_revision=1)

    all_entities = run(card, actions.graph)["entities"]
    assert {item["name"] for item in all_entities} == {"Cu", "Steel"}

    default_group_id = run(card, actions.overview)["collection"]["id"]
    scoped = run(card, actions.graph, group_id=default_group_id)["entities"]
    assert {item["name"] for item in scoped} == {"Cu"}
    scoped_alloy = run(card, actions.graph, group_id=alloys["id"])["entities"]
    assert {item["name"] for item in scoped_alloy} == {"Steel"}


EXPERIMENT_SCHEMA = {
    "type": "object", "required": ["sample_id", "conductivity"],
    "properties": {"sample_id": {"type": "string"}, "conductivity": {"type": "number"}},
}


def _experiment_schema(card):
    return run(card, actions.schemas, operation="create", name="Conductivity run",
              definition=EXPERIMENT_SCHEMA, system_prompt="x", kind="experiment")["schema"]


def _bundle(card, schema, name="Batch 7", notes=""):
    return run(card, actions.experiment_bundle_manage, operation="create",
               schema_id=schema["id"], name=name, notes=notes)["bundle"]


def _upload(card, bundle, text, filename, media_type="text/markdown"):
    """Upload one file into a bundle and wait for the conversion it queues itself."""
    result = run(card, actions.experiment_ingest, bundle_id=bundle["id"], filename=filename,
                 content_base64=base64.b64encode(text.encode()).decode(), media_type=media_type)
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.wait(result["job"]["id"], timeout=60)
    assert job.status == "COMPLETED", job.error
    return result["source"]["id"]


def test_an_experiment_upload_is_projected_into_several_reviewed_records(card):
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema, notes="Conductivity is in S/cm; the photo shows batch 7.")
    assert bundle["status"] == "ready" and bundle["files"] == [] and bundle["records"] == 0

    sheet = _upload(card, bundle, "sample,conductivity\nA1,1.2\nA2,0.9\n", "batch7.csv", "text/csv")
    note = _upload(card, bundle, "# Notes\n\nA2 was cracked after pressing.\n\n"
                   + "Pressing log line.\n" * 40, "notes.md")

    # The upload's files are the Experiment section's, not Literature's.
    assert run(card, actions.sources)["sources"] == []
    tagged = run(card, actions.sources, workflow="experiment")["sources"]
    assert {item["filename"] for item in tagged} == {"batch7.csv", "notes.md"}
    assert {item["workflow"] for item in tagged} == {"experiment"}
    assert len(run(card, actions.sources, workflow="all")["sources"]) == 2

    listed = run(card, actions.experiment_bundles, schema_id=schema["id"])["bundles"]
    assert [item["id"] for item in listed] == [bundle["id"]]
    assert [f["state"] for f in listed[0]["files"]] == ["converted", "converted"]

    # One document: both files in upload order, each under a heading that names it.
    prompt = run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])
    assert prompt["schema_id"] == schema["id"] and prompt["definition"] == EXPERIMENT_SCHEMA
    assert prompt["notes"].startswith("Conductivity is in S/cm")
    assert [f["source_id"] for f in prompt["files"]] == [sheet, note]
    assert prompt["markdown"].index(f"## File: batch7.csv (source_id={sheet})") < \
        prompt["markdown"].index(f"## File: notes.md (source_id={note})")
    assert "| A1 |" in prompt["markdown"] and "cracked" in prompt["markdown"]
    assert prompt["has_more"] is False and "records" in prompt["instruction"]
    page = run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"], limit=500)
    assert page["has_more"] is True and len(page["markdown"]) == 500
    assert page["total_characters"] == prompt["total_characters"]

    saved = run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
        {"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.2}, "source_ids": [sheet]},
        {"name": "A2", "data": {"sample_id": "A2", "conductivity": 0.9}, "source_ids": [sheet, note],
         "note": "cracked, see notes.md"},
        {"name": "A3", "data": {"sample_id": "A3"}},
    ])
    assert saved["invalid"] == 1
    by_name = {item["name"]: item for item in saved["records"]}
    assert by_name["A1"]["status"] == "draft" and by_name["A1"]["bundle_id"] == bundle["id"]
    assert [s["filename"] for s in by_name["A1"]["sources"]] == ["batch7.csv"]
    assert {s["source_id"] for s in by_name["A2"]["sources"]} == {sheet, note}
    # Uncited records are linked to every file in the upload.
    assert {s["source_id"] for s in by_name["A3"]["sources"]} == {sheet, note}
    assert by_name["A3"]["validation"]["valid"] is False
    assert run(card, actions.experiment_bundles, operation="get", bundle_id=bundle["id"])["bundle"]["status"] == "extracted"

    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
            {"name": "X", "data": {}, "source_ids": [str(uuid.uuid4())]}])

    listed = run(card, actions.experiments, bundle_id=bundle["id"])["experiments"]
    assert {item["name"] for item in listed} == {"A1", "A2", "A3"}
    assert run(card, actions.overview)["counts"]["experiment_records"] == 3

    # A person fixes the invalid one; the edit is re-validated.
    fixed = run(card, actions.experiment_update, record_id=by_name["A3"]["id"],
                data={"sample_id": "A3", "conductivity": 1.1}, expected_revision=1)["experiment"]
    assert fixed["revision"] == 2 and fixed["validation"]["valid"] is True

    # Publishing asks first, then marks the record approved and freezes it.
    asked = run(card, actions.experiment_review, operation="publish", record_id=by_name["A1"]["id"])
    assert asked["status"] == "confirmation_required"
    card.confirmed = True
    published = run(card, actions.experiment_review, operation="publish",
                    record_id=by_name["A1"]["id"], expected_revision=1)["experiment"]
    card.confirmed = False
    assert published["status"] == "published"
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_update, record_id=published["id"], name="A1b")
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_review, operation="delete", record_id=published["id"])
    assert {item["name"] for item in run(card, actions.experiments, status="published")["experiments"]} == {"A1"}

    # Re-extraction replaces the drafts but keeps what a person already published.
    again = run(card, actions.experiment_save, bundle_id=bundle["id"], replace_drafts=True, records=[
        {"name": "A2", "data": {"sample_id": "A2", "conductivity": 0.95}}])
    assert len(again["records"]) == 1
    remaining = run(card, actions.experiments, bundle_id=bundle["id"])["experiments"]
    assert {item["name"] for item in remaining} == {"A1", "A2"}

    reopened = run(card, actions.experiment_review, operation="unpublish",
                   record_id=published["id"])["experiment"]
    assert reopened["status"] == "draft"
    run(card, actions.experiment_review, operation="delete", record_id=reopened["id"])
    assert {item["name"] for item in run(card, actions.experiments, bundle_id=bundle["id"])["experiments"]} == {"A2"}


def test_a_record_that_fails_its_schema_cannot_be_published(card):
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    _upload(card, bundle, "A9\n", "a.md")
    saved = run(card, actions.experiment_save, bundle_id=bundle["id"],
                records=[{"name": "A9", "data": {"sample_id": "A9"}}])
    card.confirmed = True
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_review, operation="publish", record_id=saved["records"][0]["id"])


def test_extraction_waits_for_every_file_and_an_upload_needs_an_experiment_schema(card):
    literature = run(card, actions.schemas, operation="create", name="Literature schema",
                     definition=SCHEMA, system_prompt="x")["schema"]
    with pytest.raises(KnowledgeError):
        _bundle(card, literature)

    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_save, bundle_id=bundle["id"],
            records=[{"name": "A", "data": {}}])

    # Queue a file but do not wait for it: the prompt refuses until it is converted.
    result = run(card, actions.experiment_ingest, bundle_id=bundle["id"], filename="a.md",
                 content_base64=base64.b64encode(b"# A\n").decode(), media_type="text/markdown")
    kb = client.open_client(card.node_id, card.storage_path)
    job = kb.jobs.get(result["job"]["id"])
    if job.status != "COMPLETED":
        with pytest.raises(KnowledgeError, match="Not every file is converted yet"):
            run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])
    kb.jobs.wait(result["job"]["id"], timeout=60)
    assert run(card, actions.experiment_extract_prompt, bundle_id=bundle["id"])["has_more"] is False


def test_deleting_an_upload_removes_its_records_and_keeps_its_files_out_of_literature(card):
    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    _upload(card, bundle, "A1 1.0\n", "a.md")
    run(card, actions.experiment_save, bundle_id=bundle["id"],
        records=[{"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.0}}])

    marked = run(card, actions.experiment_bundle_manage, operation="update", bundle_id=bundle["id"],
                 status="extracting", run_id="run-1")["bundle"]
    assert (marked["status"], marked["run_id"]) == ("extracting", "run-1")
    renamed = run(card, actions.experiment_bundle_manage, operation="update", bundle_id=bundle["id"],
                  name="Batch 7b")["bundle"]
    assert renamed["run_id"] == "run-1" and renamed["name"] == "Batch 7b"
    # The schema is fixed once records exist, and can only ever be an experiment schema.
    other = run(card, actions.schemas, operation="create", name="Other run",
                definition=EXPERIMENT_SCHEMA, system_prompt="x", kind="experiment")["schema"]
    with pytest.raises(KnowledgeError, match="already has records"):
        run(card, actions.experiment_bundle_manage, operation="update", bundle_id=bundle["id"],
            schema_id=other["id"])
    empty = _bundle(card, schema, name="Empty")
    literature = run(card, actions.schemas, operation="create", name="Lit", definition=SCHEMA,
                     system_prompt="x")["schema"]
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_bundle_manage, operation="update", bundle_id=empty["id"],
            schema_id=literature["id"])
    moved = run(card, actions.experiment_bundle_manage, operation="update", bundle_id=empty["id"],
                schema_id=other["id"])["bundle"]
    assert moved["schema_id"] == other["id"]
    run(card, actions.experiment_bundle_manage, operation="delete", bundle_id=empty["id"])

    removed = run(card, actions.experiment_bundle_manage, operation="delete", bundle_id=bundle["id"])
    assert removed["records_removed"] == 1
    assert run(card, actions.experiment_bundles)["bundles"] == []
    assert run(card, actions.experiments)["experiments"] == []
    assert run(card, actions.sources)["sources"] == []
    assert len(run(card, actions.sources, workflow="experiment")["sources"]) == 1
    with pytest.raises(KnowledgeError):
        run(card, actions.experiment_ingest, bundle_id=bundle["id"], filename="b.md",
            content_base64=base64.b64encode(b"x").decode())


def test_chart_data_source_reads_published_experiment_records_only(card):
    # data_source.py is the one module besides plugin.py that imports OAW itself.
    pytest.importorskip("open_agent_world", reason="Run inside the OAW backend environment")
    from oaw_knowledge_base import data_source

    schema = _experiment_schema(card)
    bundle = _bundle(card, schema)
    _upload(card, bundle, "x\n", "a.md")
    saved = run(card, actions.experiment_save, bundle_id=bundle["id"], records=[
        {"name": "A1", "data": {"sample_id": "A1", "conductivity": 1.2}},
        {"name": "A2", "data": {"sample_id": "A2", "conductivity": 0.9}}])
    card.confirmed = True
    run(card, actions.experiment_review, operation="publish", record_id=saved["records"][0]["id"])

    class Query:
        schema_id = f"experiments:{schema['id']}"
        aggregate = "none"
        columns = ["sample_id", "conductivity"]
        order_by = None
        descending = False
        limit = 50
        entity_type = relation_type = None

    table = data_source.read(card, Query())
    assert table["rows"] == [["A1", 1.2]]
