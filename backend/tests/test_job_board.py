"""Real board/API permissions and persistence with a controlled compute provider."""
from types import SimpleNamespace

import pytest

from backend.plugins.background_jobs import BackgroundJobProvider, JobObservation, JobSubmit
from backend.runs.manager import _current_invocation
from backend.runs.models import RunStatus
from backend.tests.conftest import create_node
from backend.tests.plugin_support import install_test_plugin
from backend.tests.test_background_jobs import Spec


def connect(client, source, target, relationship):
    response = client.post('/api/edges', json={'source': source, 'target': target, 'relationship': relationship})
    assert response.status_code == 201, response.text
    return response.json()['id']


@pytest.fixture
def board(client):
    services = client.app.state.services
    services.node_execution.continuation_closed = True
    compute = SimpleNamespace(probes=0, cancellations=0, status='running', cancel_hook=None)

    async def inspect(context, external_id):
        compute.probes += 1
        return JobObservation(status=compute.status, detail='Provider observation')

    async def cancel(context, external_id):
        compute.cancellations += 1
        if compute.cancel_hook:
            await compute.cancel_hook()

    install_test_plugin(services.plugins, 'test.jobs', lambda registration:
        registration.register_background_job_provider('test.compute', BackgroundJobProvider(
            specification=Spec, inspect=inspect, cancel=cancel)))
    agent = create_node(client, 'agent')['id']
    sandbox = create_node(client, 'sandbox')['id']
    view = create_node(client, 'core.jobs')['id']
    conversation = create_node(client, 'conversation')['id']
    grant = connect(client, agent, sandbox, 'execute')
    connect(client, agent, conversation, 'participate')
    sessions = [client.post(f'/api/conversations/{conversation}/sessions', json={
        'title': title, 'participant_ids': [agent]}).json()['id'] for title in ('A', 'B')]
    jobs = []
    for index, session in enumerate(sessions):
        run = services.run_manager.store.create(agent_id=agent, runtime_provider_id='core.mock',
            caller_kind='conversation', caller_id=conversation, context_id=session)
        services.run_manager.store.update_status(run.run_id, RunStatus.RUNNING)
        token = _current_invocation.set(SimpleNamespace(agent_id=agent, run_id=run.run_id, context_id=session))
        try:
            job, _ = services.background_jobs.reserve(agent, JobSubmit(provider='test.compute', sandbox_id=sandbox,
                request_key=f'calculation-{index}'), f'fingerprint-{index}')
            jobs.append(services.background_jobs.update(job['id'], status='running', external_id=f'external-{index}',
                wake_requested=False, next_check_at=10**15))
        finally:
            _current_invocation.reset(token)
        services.run_manager.store.update_status(run.run_id, RunStatus.SUCCEEDED)
    return SimpleNamespace(services=services, compute=compute, agent=agent, sandbox=sandbox, view=view,
        sessions=sessions, jobs=jobs, grant=grant, url=f'/api/nodes/{view}/jobs', headers={'X-OAW-State-Session': sessions[0]})


def control(client, board, action, *, revision=None, job=None):
    job = job or board.jobs[0]
    return client.post(f'{board.url}/{job["id"]}/actions', headers=board.headers,
        json={'action': action, 'revision': revision or board.services.background_jobs.get(job['id'])['revision']})


def test_board_is_connected_session_scoped_and_observation_is_read_only(client, board):
    assert client.get(board.url, headers=board.headers).json() == {'sources': [], 'jobs': []}
    edge = connect(client, board.view, board.agent, 'core.jobs-source')
    value = client.get(board.url, headers=board.headers).json()
    assert [job['id'] for job in value['jobs']] == [board.jobs[0]['id']]
    assert value['jobs'][0]['actions'] == []
    assert not {'fingerprint', 'specification', 'context_id'} & value['jobs'][0].keys()
    assert board.compute.probes == 0
    assert control(client, board, 'cancel').status_code == 403
    assert client.get(board.url, headers={'X-OAW-State-Session': board.sessions[1]}).json()['jobs'][0]['id'] == board.jobs[1]['id']
    assert client.delete('/api/edges/' + edge).status_code == 200
    assert client.get(board.url, headers=board.headers).json()['jobs'] == []
    assert client.get(f'/api/nodes/{board.agent}/jobs').status_code == 403


def test_manual_controls_do_not_require_an_active_agent_or_submit_work(client, board):
    connect(client, board.view, board.agent, 'core.jobs-manage')
    assert control(client, board, 'pause').json()['paused'] is True
    assert board.compute.cancellations == 0
    assert control(client, board, 'resume').json()['paused'] is False
    original = board.jobs[0]
    current = board.services.background_jobs.get(original['id'])
    assert current['wake_run_id'] == original['wake_run_id'] and current['wake_requested'] is False
    assert control(client, board, 'refresh').json()['status'] == 'running'
    assert board.compute.probes == 1
    assert control(client, board, 'pause', revision=original['revision']).status_code == 409
    assert control(client, board, 'cancel', job=board.jobs[1]).status_code == 403


def test_cancel_requires_observed_termination_and_cannot_cancel_finished_job(client, board):
    connect(client, board.view, board.agent, 'core.jobs-manage')
    value = control(client, board, 'cancel')
    assert value.status_code == 200, value.text
    assert value.json()['status'] == 'running' and value.json()['paused']
    assert board.compute.cancellations == 1
    board.compute.status = 'cancelled'
    value = control(client, board, 'refresh').json()
    assert value['status'] == 'cancelled' and 'cancel' not in value['actions']
    assert control(client, board, 'cancel').status_code == 403


def test_sandbox_revocation_hides_jobs_and_blocks_control(client, board):
    connect(client, board.view, board.agent, 'core.jobs-manage')
    assert client.delete('/api/edges/' + board.grant).status_code == 200
    assert client.get(board.url, headers=board.headers).json()['jobs'] == []
    assert control(client, board, 'cancel').status_code == 403
    assert board.compute.cancellations == 0


def test_board_rechecks_connection_after_provider_call(client, board):
    edge = connect(client, board.view, board.agent, 'core.jobs-manage')

    async def revoke():
        await board.services.delete_edge(edge)

    board.compute.cancel_hook = revoke
    assert control(client, board, 'cancel').status_code == 403
    job = board.services.background_jobs.get(board.jobs[0]['id'])
    assert job['status'] == 'running' and job['paused']
    assert 'unconfirmed' in job['detail']


def test_resume_after_stop_monitors_compute_without_reviving_the_stopped_turn(client, board):
    connect(client, board.view, board.agent, 'core.jobs-manage')
    job = board.jobs[0]
    board.services.run_manager.store.update_lifecycle(job['source_run_id'], cancellation_requested=True, cancellation_origin='user')
    client.portal.call(board.services.background_jobs.tick)
    assert board.services.background_jobs.get(job['id'])['paused']
    assert control(client, board, 'resume').status_code == 200
    client.portal.call(board.services.background_jobs.tick)
    current = board.services.background_jobs.get(job['id'])
    assert current['monitor_after_stop'] and not current['paused'] and not current['wake_requested']
    assert current['notification_run_id'] is None
