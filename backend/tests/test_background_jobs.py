"""Real host persistence/permissions with controlled compute and model providers."""
import asyncio
from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict

from backend.capabilities.provider import WorldAgentCapabilityProvider
from backend.conversations.models import ConversationSessionCreate
from backend.errors import ConflictError, PermissionDeniedError
from backend.plugins.background_jobs import BackgroundJobProvider, JobObservation
from backend.services import create_services
from backend.api.card_library import plugin_usage
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_node_delegation import research, until


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = "md"


class Compute:
    status = "running"
    submissions = 0
    probes = 0
    fail_submit = False
    fail_probe = False

    async def submit(self, context, spec, request_id):
        self.submissions += 1
        await asyncio.sleep(0)
        if self.fail_submit:
            raise TimeoutError("response lost after remote admission")
        return "remote-123"

    async def inspect(self, context, external_id):
        self.probes += 1
        if self.fail_probe:
            raise ConnectionError("offline")
        return JobObservation(status=self.status)

    async def cancel(self, context, external_id):
        pass  # Request acceptance is deliberately not termination evidence.


def setup(research):
    services, ids, runtime, _ = research
    compute = Compute()
    install_test_plugin(services.plugins, "test.jobs", lambda registration:
        registration.register_background_job_provider("test.compute", BackgroundJobProvider(
            specification=Spec, submit=compute.submit, inspect=compute.inspect, cancel=compute.cancel, poll_interval_seconds=1)))
    services.node_execution.continuation_closed = True
    async def invoke(name, **args):
        return await WorldAgentCapabilityProvider(services).invoke_tool(ids["agent"], "host:" + name, args)
    request = dict(provider="test.compute", sandbox_id=ids["sandbox"], request_key="plan:md", specification={})
    return services, ids, runtime, compute, invoke, request


@pytest.mark.asyncio
async def test_submission_dedup_and_unknown_outcome_never_replay(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    compute.fail_submit = True
    captured = []
    async def script(context):
        captured.append(await invoke("submit_background_job", **request))
        captured.append(await invoke("submit_background_job", **{**request, "specification": {"name": "md"}}))
        with pytest.raises(ConflictError):
            await invoke("submit_background_job", **{**request, "specification": {"name": "other"}})
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(run.run_id)
    assert not runtime.errors
    assert compute.submissions == 1 and captured[0]["status"] == "unknown"
    assert captured[0]["id"] == captured[1]["id"]
    job = services.background_jobs.get(captured[0]["id"])
    assert "specification" not in job  # Credential-bearing specs are never journaled.


@pytest.mark.asyncio
async def test_concurrent_submission_reserves_one_identity_before_side_effect(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    async def script(context):
        first, second = await asyncio.gather(invoke("submit_background_job", **request), invoke("submit_background_job", **request))
        assert first["id"] == second["id"]
        assert services.background_jobs.get(first["id"])["external_id"] == "remote-123"
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Submit concurrently")
    await services.run_manager.wait_execution(root.run_id)
    assert not runtime.errors and compute.submissions == 1


@pytest.mark.asyncio
async def test_monitor_is_model_free_and_terminal_notification_admitted_once(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
            await invoke("wait_background_jobs", job_ids=[captured["id"]])
        else:
            assert (await invoke("inspect_background_jobs", job_ids=[captured["id"]]))["jobs"][0]["status"] == "succeeded"
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(root.run_id)
    for _ in range(70):
        services.background_jobs.update(captured["id"], next_check_at=0)
        await services.background_jobs.tick()
        await asyncio.gather(*services.background_jobs.probes.values())
    assert len(runtime.contexts) == 1 and compute.probes == 70 and compute.submissions == 1
    compute.status = "succeeded"
    services.background_jobs.update(captured["id"], next_check_at=0)
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    await until(lambda: len(runtime.contexts) == 2)
    await services.run_manager.wait_execution(runtime.contexts[-1].run_id)
    await services.background_jobs.tick()
    assert len(runtime.contexts) == 2 and not runtime.errors
    assert runtime.contexts[-1].root_run_id == root.run_id


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["run", "permission"])
async def test_stop_and_revocation_pause_automation_without_remote_cancellation(research, stop):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        captured.update(await invoke("submit_background_job", **request))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(root.run_id)
    if stop == "run":
        await services.run_manager.cancel_run(root.run_id)
    else:
        edge = next(e for e in services.world.list_edges() if e.source == ids["agent"] and e.target == ids["sandbox"])
        await services.delete_edge(edge.id)
    await services.background_jobs.tick()
    job = services.background_jobs.get(captured["id"])
    assert job["paused"] and job["status"] == "queued" and compute.probes == 0
    assert len(runtime.contexts) == 1


@pytest.mark.asyncio
async def test_job_reads_reauthorize_session_and_transient_probe_is_not_failure(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    origin = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="Origin", participant_ids=[ids["agent"]]))
    other = services.conversations.create_session(ids["conversation"], ConversationSessionCreate(title="Other", participant_ids=[ids["agent"]]))
    captured = {}
    async def script(context):
        if context.context_id == origin.id:
            captured.update(await invoke("submit_background_job", **request))
        else:
            with pytest.raises(PermissionDeniedError):
                await invoke("inspect_background_jobs", job_ids=[captured["id"]])
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit", caller_kind="conversation", caller_id=ids["conversation"], context_id=origin.id)
    await services.run_manager.wait_execution(root.run_id)
    compute.fail_probe = True
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    job = services.background_jobs.get(captured["id"])
    assert job["status"] == "queued" and job["failures"] == 1 and job["next_check_at"] > job["updated_at"]
    second = await services.run_manager.start_run(ids["agent"], "other", caller_kind="conversation", caller_id=ids["conversation"], context_id=other.id)
    await services.run_manager.wait_execution(second.run_id)
    assert not runtime.errors


@pytest.mark.asyncio
async def test_restart_recovers_pending_job_and_unadmitted_claim_only(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "submit")
    await services.run_manager.wait_execution(root.run_id)
    services.background_jobs.update(captured["id"], notification_run_id="unadmitted-reservation")
    await services.shutdown()
    compute.status = "succeeded"
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        await until(lambda: len(runtime.contexts) == 2)
        await restarted.run_manager.wait_execution(runtime.contexts[-1].run_id)
        assert compute.submissions == 1 and not runtime.errors
        await restarted.shutdown()
        again = create_services(services.settings, plugins=services.plugins)
        try:
            await again.startup()
            await again.background_jobs.tick()
            assert len(runtime.contexts) == 2
        finally:
            await again.shutdown()
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_uncertain_submission_can_attach_but_cancel_requires_terminal_observation(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    compute.fail_submit = True
    captured = {}
    async def script(context):
        captured.update(await invoke("submit_background_job", **request))
        attached = await invoke("attach_background_job", provider=request["provider"], sandbox_id=ids["sandbox"],
            request_key=request["request_key"], external_id="remote-123", job_id=captured["id"])
        assert attached["id"] == captured["id"] and attached["status"] == "running"
        pending = await invoke("control_background_job", job_id=captured["id"], action="cancel")
        assert pending["status"] == "running" and pending["paused"]
        compute.status = "cancelled"
        confirmed = await invoke("control_background_job", job_id=captured["id"], action="cancel")
        assert confirmed["status"] == "cancelled"
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "Reconcile")
    await services.run_manager.wait_execution(run.run_id)
    assert not runtime.errors and compute.submissions == 1


@pytest.mark.asyncio
async def test_active_job_blocks_owner_deletion_and_provider_unload_until_explicit_detach(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if not captured:
            captured.update(await invoke("submit_background_job", **request))
        else:
            await invoke("control_background_job", job_id=captured["id"], action="detach")
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "Submit")
    await services.run_manager.wait_execution(run.run_id)
    with pytest.raises(ConflictError, match="Background work"):
        await services.delete_cards([ids["sandbox"]])
    assert "tracked background work" in plugin_usage(services, "test.jobs")
    run = await services.run_manager.start_run(ids["agent"], "Relinquish monitoring")
    await services.run_manager.wait_execution(run.run_id)
    assert not runtime.errors
    assert "tracked background work" not in plugin_usage(services, "test.jobs")
    await services.delete_cards([ids["sandbox"]])
    assert services.background_jobs.get(captured["id"])["status"] == "queued"


@pytest.mark.asyncio
async def test_host_shutdown_during_active_turn_preserves_external_wakeup(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
            await asyncio.Event().wait()
    runtime.script = script
    run = await services.run_manager.start_run(ids["agent"], "Submit")
    await until(lambda: bool(captured))
    await services.shutdown()
    assert services.run_manager.get_run(run.run_id).lifecycle["cancellation_origin"] == "shutdown"
    compute.status = "succeeded"
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        await until(lambda: len(runtime.contexts) == 2)
        await restarted.run_manager.wait_execution(runtime.contexts[-1].run_id)
        assert runtime.contexts[-1].parent_run_id == run.run_id and not runtime.errors
        assert compute.submissions == 1
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_interrupted_admitted_notification_requires_reconciliation_not_replay(research):
    services, ids, runtime, compute, invoke, request = setup(research)
    captured = {}
    async def script(context):
        if context.caller.kind != "delegation":
            captured.update(await invoke("submit_background_job", **request))
        else:
            await asyncio.Event().wait()
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Submit")
    await services.run_manager.wait_execution(root.run_id)
    compute.status = "succeeded"
    await services.background_jobs.tick()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    await until(lambda: len(runtime.contexts) == 2)
    await services.shutdown()
    restarted = create_services(services.settings, plugins=services.plugins)
    try:
        await restarted.startup()
        await restarted.background_jobs.tick()
        job = restarted.background_jobs.get(captured["id"])
        assert job["paused"] and job["notification_run_id"] == runtime.contexts[-1].run_id
        assert len(runtime.contexts) == 2 and compute.submissions == 1
    finally:
        await restarted.shutdown()


@pytest.mark.asyncio
async def test_revocation_during_probe_cannot_publish_completion(research, monkeypatch):
    services, ids, runtime, compute, invoke, request = setup(research)
    started, release = asyncio.Event(), asyncio.Event()
    definition = services.plugins.background_job_provider("test.compute")
    async def inspect(context, external_id):
        started.set()
        await release.wait()
        return JobObservation(status="succeeded")
    monkeypatch.setitem(services.plugins._background_job_providers, "test.compute", replace(definition, inspect=inspect))
    captured = {}
    async def script(context):
        captured.update(await invoke("submit_background_job", **request))
    runtime.script = script
    root = await services.run_manager.start_run(ids["agent"], "Submit")
    await services.run_manager.wait_execution(root.run_id)
    await services.background_jobs.tick()
    await started.wait()
    edge = next(e for e in services.world.list_edges() if e.source == ids["agent"] and e.target == ids["sandbox"])
    await services.delete_edge(edge.id)
    release.set()
    await asyncio.gather(*services.background_jobs.probes.values())
    await services.background_jobs.tick()
    job = services.background_jobs.get(captured["id"])
    assert job["status"] == "queued" and job["paused"] and len(runtime.contexts) == 1
    async def inspect_remaining(context):
        assert (await invoke("inspect_background_jobs"))["jobs"] == []
        with pytest.raises(PermissionDeniedError):
            await invoke("inspect_background_jobs", job_ids=[captured["id"]])
    runtime.script = inspect_remaining
    followup = await services.run_manager.start_run(ids["agent"], "Inspect authorized work")
    await services.run_manager.wait_execution(followup.run_id)
    assert not runtime.errors
