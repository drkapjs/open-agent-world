"""Execution sources stay read-only, connected, session-scoped and provider-neutral."""
from backend.tests.conftest import create_node
from backend.tests.test_summoning import stock, invoke, settle
from backend.runs.models import RunStatus
from backend.tests.plugin_support import install_test_plugin
from open_agent_world.plugin_api import NodeTypeDefinition, NodeExecutionSource, ExecutionSnapshot, ScopedStateSpec
from pydantic import BaseModel
import pytest


def connect(client, graph, source, relationship='core.execution-source'):
    response = client.post('/api/edges', json={'source': graph['id'], 'target': source['id'], 'relationship': relationship})
    assert response.status_code == 201, response.text
    return response.json()


def read(client, graph, source, session='default'):
    return client.get(f"/api/nodes/{graph['id']}/execution-sources/{source['id']}", headers={'X-OAW-State-Session': session})


def test_agent_run_hierarchy_uses_ancestry_and_revocation(client):
    graph = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent', name='Coordinator')
    worker = create_node(client, 'agent', name='Worker')
    conversation = create_node(client, 'conversation')
    session = client.get('/api/conversations/' + conversation['id']).json()['sessions'][0]['id']
    store = client.app.state.services.run_manager.store
    root = store.create(agent_id=agent['id'], runtime_provider_id='core.mock', caller_kind='conversation', caller_id=conversation['id'], context_id=session)
    child = store.create(agent_id=worker['id'], runtime_provider_id='core.mock', caller_kind='summon', parent_run_id=root.run_id, context_id='summon:private')
    store.update_status(root.run_id, RunStatus.WAITING)
    store.update_status(child.run_id, RunStatus.SUCCEEDED)
    assert read(client, graph, agent, session).status_code == 403
    edge = connect(client, graph, agent)
    response = read(client, graph, agent, session)
    assert response.status_code == 200, response.text
    nodes = response.json()['nodes']
    assert [(n['label'], n['parent_id']) for n in nodes] == [('Coordinator', None), ('Worker', root.run_id)]
    assert nodes[1]['status'] == 'succeeded'
    assert read(client, graph, agent).json()['nodes'] == []
    assert client.get(f"/api/nodes/{graph['id']}/execution-sources").json()['sources'][0]['id'] == agent['id']
    assert client.delete('/api/edges/' + edge['id']).status_code == 200
    assert read(client, graph, agent, session).status_code == 403
    assert read(client, worker, agent, session).status_code == 403


def test_barracks_and_summoning_adapter_show_real_attempts_and_recheck_library_connection(client):
    graph = create_node(client, 'core.execution-graph')
    box = create_node(client, 'oaw.barracks')
    worker = create_node(client, 'agent', name='Researcher', config={'runtime_provider_id': 'core.mock'})
    adapter = create_node(client, 'oaw.barracks.summoner')
    stock(client, box, worker)
    connect(client, graph, box)
    connect(client, graph, adapter)
    library_edge = connect(client, adapter, box, 'oaw.barracks.summon')
    instance = settle(client, box, invoke(client, box, action='summon', agent_id=worker['id'], prompt='Inspect copper'))
    followup = settle(client, box, invoke(client, box, action='message', instance_id=instance['id'], prompt='Verify result'))
    for source in [box, adapter]:
        response = read(client, graph, source)
        assert response.status_code == 200, response.text
        nodes = response.json()['nodes']
        assert {n['id'] for n in nodes} == {instance['run_id'], followup['run_id']}
        assert all(n['status'] == 'succeeded' for n in nodes)
        assert any('Verify result' in n['result'] for n in nodes)
    assert client.delete('/api/edges/' + library_edge['id']).status_code == 200
    assert read(client, graph, adapter).json()['nodes'] == []
    assert len(read(client, graph, box).json()['nodes']) == 2


def test_custom_source_uses_public_contract_and_cannot_be_read_unconnected(client):
    class Config(BaseModel):
        pass
    def register(registration):
        registration.register_node_type(NodeTypeDefinition(id='test.graph.source', label='Custom executor', description='Fixture', icon='workflow',
            color='#888888', deck_id='tools', deck_label='Tools', deck_icon='boxes', default_name='Custom executor', default_size=(300,200),
            default_status='available', statuses=frozenset({'available'}), config_model=Config,
            state=ScopedStateSpec(supported_scopes=('session',), default_scope='session'),
            execution_source=NodeExecutionSource(read=lambda context: ExecutionSnapshot.model_validate(context.state.get()['value']).model_dump())))
    services = client.app.state.services
    install_test_plugin(services.plugins, 'test.graph', register)
    source = create_node(client, 'test.graph.source')
    graph = create_node(client, 'core.execution-graph')
    services.card_state.bind(source['id']).set({'title': 'External workflow', 'nodes': [{'id': 'x', 'label': 'Tool', 'kind': 'tool', 'status': 'waiting'}]})
    assert read(client, graph, source).status_code == 403
    connect(client, graph, source)
    result = read(client, graph, source)
    assert result.status_code == 200, result.text
    assert result.json()['nodes'][0]['kind'] == 'tool'
    assert result.json()['title'] == 'External workflow'
    assert client.post(f"/api/nodes/{graph['id']}/execution-sources/{source['id']}", json={}).status_code == 405


@pytest.mark.parametrize('new_status', [RunStatus.CREATED, RunStatus.FAILED])
def test_new_send_cannot_hide_a_still_running_detailed_graph(client, new_status):
    graph = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent')
    connect(client, graph, agent)
    store = client.app.state.services.run_manager.store
    running = store.create(agent_id=agent['id'], runtime_provider_id='core.mock', caller_kind='direct')
    store.update_status(running.run_id, RunStatus.RUNNING)
    store.update_lifecycle(running.run_id, execution_graph={'title': 'Active task', 'phase': 'running', 'nodes': [
        {'id': 'main', 'label': 'Coordinator', 'status': 'running'},
        {'id': 'child', 'label': 'Worker', 'status': 'running', 'parent_id': 'main'},
    ]})
    newer = store.create(agent_id=agent['id'], runtime_provider_id='core.mock', caller_kind='direct')
    store.update_status(newer.run_id, new_status)
    value = read(client, graph, agent).json()
    assert value['run_id'] == running.run_id
    assert [n['id'] for n in value['nodes']] == ['main', 'child']
    assert value['previous_run_id'] == ''
    # The finished child and its parent remain visible when the old Run settles,
    # even if the newer send never managed to start a provider.
    store.update_status(running.run_id, RunStatus.SUCCEEDED)
    value = read(client, graph, agent).json()
    assert value['run_id'] == newer.run_id
    assert value['previous_run_id'] == running.run_id
    assert value['phase'] == new_status.value
    # Completing a chat turn is not evidence that provider work succeeded.
    assert [n['status'] for n in value['nodes']] == ['running', 'running']


def test_retained_projection_is_replaced_and_never_crosses_session_or_provider(client):
    graph = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent')
    connect(client, graph, agent)
    store = client.app.state.services.run_manager.store
    old = store.create(agent_id=agent['id'], runtime_provider_id='core.mock', caller_kind='direct')
    store.update_status(old.run_id, RunStatus.SUCCEEDED)
    store.update_lifecycle(old.run_id, execution_graph={'nodes': [
        {'id': 'old', 'label': 'Old attempt', 'status': 'failed'},
    ]})
    current = store.create(agent_id=agent['id'], runtime_provider_id='core.mock', caller_kind='direct')
    store.update_status(current.run_id, RunStatus.RUNNING)
    value = read(client, graph, agent).json()
    assert value['previous_run_id'] == old.run_id
    assert value['phase'] == 'running'
    assert value['nodes'][0]['status'] == 'failed'  # Never paint history as running.
    conversation = create_node(client, 'conversation')
    other_session = client.get('/api/conversations/' + conversation['id']).json()['sessions'][0]['id']
    assert read(client, graph, agent, other_session).json()['nodes'] == []
    store.update_lifecycle(current.run_id, execution_graph={'nodes': [{'id': 'new', 'label': 'New work', 'status': 'running'}]})
    value = read(client, graph, agent).json()
    assert value['previous_run_id'] == ''
    assert [n['id'] for n in value['nodes']] == ['new']
    store.update_lifecycle(current.run_id, execution_graph={'nodes': []})
    assert read(client, graph, agent).json()['nodes'] == []
    store.update_status(current.run_id, RunStatus.SUCCEEDED)
    different = store.create(agent_id=agent['id'], runtime_provider_id='different-provider', caller_kind='direct')
    value = read(client, graph, agent).json()
    assert value['previous_run_id'] == ''
    assert value['run_id'] == different.run_id
    assert [n['id'] for n in value['nodes']] == [different.run_id]


@pytest.mark.parametrize('status', [RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED])
def test_terminal_turn_never_claims_pending_work_executed(client, status):
    graph = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent')
    connect(client, graph, agent)
    store = client.app.state.services.run_manager.store
    run = store.create(agent_id=agent['id'], runtime_provider_id='test', caller_kind='direct')
    original = ['pending', 'waiting', 'running', 'success', 'needs_replanning']
    store.update_lifecycle(run.run_id, execution_graph={'nodes': [
        {'id': str(index), 'label': value, 'status': value} for index, value in enumerate(original)
    ]})
    store.update_status(run.run_id, status)
    expected = original if status == RunStatus.SUCCEEDED else [*original[:2], status.value, *original[3:]]
    assert [node['status'] for node in read(client, graph, agent).json()['nodes']] == expected


def test_same_timestamp_runs_use_creation_order_not_random_uuid(client, monkeypatch):
    import backend.runs.store as store_module
    monkeypatch.setattr(store_module, '_now', lambda: '2026-10-13T00:00:00+00:00')
    graph = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent')
    connect(client, graph, agent)
    store = client.app.state.services.run_manager.store
    first = store.create(run_id='z-first', agent_id=agent['id'], runtime_provider_id='test', caller_kind='direct')
    store.update_lifecycle(first.run_id, execution_graph={'nodes': [{'id': 'plan', 'label': 'Plan', 'status': 'waiting'}]})
    store.update_status(first.run_id, RunStatus.SUCCEEDED)
    second = store.create(run_id='a-second', agent_id=agent['id'], runtime_provider_id='test', caller_kind='direct')
    store.update_status(second.run_id, RunStatus.RUNNING)
    assert [run.run_id for run in store.list(agent_id=agent['id'])] == ['z-first', 'a-second']
    value = read(client, graph, agent).json()
    assert (value['run_id'], value['previous_run_id']) == ('a-second', 'z-first')
    assert value['nodes'][0]['status'] == 'waiting'


def test_legacy_persisted_dependencies_promote_only_declared_control_edges(client):
    graph = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent')
    connect(client, graph, agent)
    store = client.app.state.services.run_manager.store
    run = store.create(agent_id=agent['id'], runtime_provider_id='test', caller_kind='direct')
    nodes = [{'id': 'a', 'label': 'Earlier'},
             {'id': 'b', 'label': 'Contained', 'parent_id': 'a'},
             {'id': 'c', 'label': 'Dependent', 'dependency_ids': ['a', 'a']}]
    store.update_lifecycle(run.run_id, execution_graph={'nodes': nodes})
    edges = read(client, graph, agent).json()['edges']
    assert [(edge['source'], edge['target'], edge['kind']) for edge in edges] == [('a', 'c', 'control')]
    assert read(client, graph, agent).json()['edges'] == edges  # Stable legacy IDs.
    store.update_lifecycle(run.run_id, execution_graph={'nodes': nodes, 'edges': []})
    assert read(client, graph, agent).json()['edges'] == []


def history_fixture(client):
    """Real persisted provider records, including sparse cross-execution edges."""
    graph = create_node(client, 'core.execution-graph')
    agent = create_node(client, 'agent')
    grant = connect(client, graph, agent)
    store = client.app.state.services.run_manager.store
    run = store.create(agent_id=agent['id'], runtime_provider_id='test', caller_kind='direct')
    nodes = [{'id': f'step-{index}', 'label': f'Step {index}', 'execution_id': f'execution-{index // 10}',
              'role': 'step', 'sequence': index, 'status': 'success'} for index in range(1200)]
    edges = [dict(id=f'e-{source}', source=f'step-{source}', target='step-1199', kind='control')
             for source in (0, 89, 1170)]
    edges += [dict(id='artifact-edge', source='step-0', target='step-1199', kind='data',
                   artifact_ref='oaw:artifact:collection:version', evidence='explicit input binding'),
              dict(id='outside-target', source='step-0', target='not-loaded', kind='data')]
    value = {'history_id': 'durable-history', 'revision': 2, 'nodes': nodes, 'edges': edges}
    store.update_lifecycle(run.run_id, execution_graph=value)
    store.update_status(run.run_id, RunStatus.SUCCEEDED)
    url = f"/api/nodes/{graph['id']}/execution-sources/{agent['id']}"
    return graph, agent, grant, run, value, url


def test_history_pages_preserve_nodes_and_sparse_edges_and_reauthorize(client):
    graph, agent, grant, run, stored, url = history_fixture(client)
    full = client.get(url).json()
    assert len(full['nodes']) == full['total_nodes'] == 1200
    assert full['mode'] == 'full' and not full['truncated']
    nodes, edges = [], []
    params = {'limit': 173}
    cursor = None
    while True:
        response = client.get(url, params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['mode'] == 'page'
        assert page['revision'] == 2 and page['history_id'] == 'durable-history'
        nodes.extend(page['nodes'])
        edges.extend(page['edges'])
        if not page['next_cursor']:
            break
        cursor = page['next_cursor']
        params['cursor'] = cursor
    assert nodes == full['nodes']
    assert edges == full['edges']
    # Parent/chronological neighbors do not acquire any fabricated edges.
    assert len(edges) == 5
    client.delete('/api/edges/' + grant['id'])
    assert client.get(url, params={'cursor': cursor}).status_code == 403


def test_changed_history_invalidates_cursor_instead_of_mixing_revisions(client):
    graph, agent, grant, run, stored, url = history_fixture(client)
    cursor = client.get(url, params={'limit': 500}).json()['next_cursor']
    stored['revision'] += 1
    stored['nodes'][0]['status'] = 'failed'
    client.app.state.services.run_manager.store.update_lifecycle(run.run_id, execution_graph=stored)
    assert client.get(url, params={'limit': 500, 'cursor': cursor}).status_code == 409
    assert client.get(url, params={'limit': 500}).json()['nodes'][0]['status'] == 'failed'
    assert client.get(url, params={'cursor': 'garbage'}).status_code == 422


def test_history_survives_new_application_instance(data_root):
    from backend.config import Settings
    from backend.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(Settings.for_data_root(data_root))) as original:
        *_, url = history_fixture(original)
        expected = original.get(url).json()
    with TestClient(create_app(Settings.for_data_root(data_root))) as reopened:
        assert reopened.get(url).json() == expected


def test_publication_revisions_are_full_and_delayed_writes_cannot_rollback(client):
    from types import SimpleNamespace
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.errors import ResourceValidationError
    from backend.runs.manager import _current_invocation
    services = client.app.state.services
    agent = create_node(client, 'agent')
    run = services.run_manager.store.create(agent_id=agent['id'], runtime_provider_id='test', caller_kind='direct')
    async def publish(value):
        token = _current_invocation.set(SimpleNamespace(agent_id=agent['id'], run_id=run.run_id))
        try:
            await WorldAgentCapabilityProvider(services).publish_execution(agent['id'], run.run_id, value)
        finally:
            _current_invocation.reset(token)
    complete = {'history_id': 'same-history', 'revision': 3, 'nodes': [
        {'id': 'x', 'label': 'Done', 'status': 'success'},
        {'id': 'next', 'label': 'Next', 'dependency_ids': ['x']},
    ]}
    client.portal.call(publish, complete)
    client.portal.call(publish, {**complete, 'revision': 2, 'nodes': []})
    persisted = services.run_manager.get_run(run.run_id).lifecycle['execution_graph']
    assert persisted['revision'] == 3 and persisted['nodes'][0]['status'] == 'success'
    assert [(edge['source'], edge['target'], edge['kind']) for edge in persisted['edges']] == [('x', 'next', 'control')]
    with pytest.raises(ResourceValidationError, match='complete snapshots'):
        client.portal.call(publish, {**complete, 'mode': 'page', 'revision': 4})
    # Explicit empty full replacement is meaningful, not an unavailable read.
    client.portal.call(publish, {**complete, 'revision': 4, 'nodes': []})
    assert services.run_manager.get_run(run.run_id).lifecycle['execution_graph']['nodes'] == []
    # The richer contract supports actual long histories above the old
    # 500-node / 2-MiB limit, while retaining an explicit bounded write budget.
    large = {**complete, 'revision': 5, 'nodes': [
        {'id': str(index), 'label': 'Step', 'detail': 'x' * 4000} for index in range(600)
    ]}
    client.portal.call(publish, large)
    assert len(services.run_manager.get_run(run.run_id).lifecycle['execution_graph']['nodes']) == 600
    for node in large['nodes']:
        node['detail'] = 'x' * 14000
    with pytest.raises(ResourceValidationError, match='8 MiB'):
        client.portal.call(publish, {**large, 'revision': 6})
    assert services.run_manager.get_run(run.run_id).lifecycle['execution_graph']['revision'] == 5


def test_history_publications_retain_attempts_across_runs_and_reload_without_crossing_scope(client):
    from types import SimpleNamespace
    from backend.capabilities.provider import WorldAgentCapabilityProvider
    from backend.runs.manager import _current_invocation
    from backend.runs.store import RunStore
    from backend.persistence.database import Database
    services = client.app.state.services
    agent = create_node(client, 'agent')
    graph = create_node(client, 'core.execution-graph')
    connect(client, graph, agent)
    store = services.run_manager.store
    first = store.create(agent_id=agent['id'], runtime_provider_id='history-test', caller_kind='direct')

    async def publish(run_id, value):
        token = _current_invocation.set(SimpleNamespace(agent_id=agent['id'], run_id=run_id))
        try:
            await WorldAgentCapabilityProvider(services).publish_execution(agent['id'], run_id, value)
        finally:
            _current_invocation.reset(token)

    base = {'history_id': 'project-session', 'retention': 'history'}
    client.portal.call(publish, first.run_id, {**base, 'revision': 1, 'nodes': [
        {'id': 'failed-attempt', 'label': 'Edit parser', 'status': 'failed',
         'actor_id': 'builder', 'workstream_id': 'fix-parser', 'operation': 'edit'}]})
    client.portal.call(publish, first.run_id, {**base, 'revision': 2, 'nodes': [
        {'id': 'retry', 'label': 'Repair parser', 'status': 'success', 'retry_of': 'failed-attempt'}],
        'edges': [{'id': 'retry-edge', 'source': 'failed-attempt', 'target': 'retry', 'kind': 'retry'}]})
    store.update_status(first.run_id, RunStatus.SUCCEEDED)
    second = store.create(agent_id=agent['id'], runtime_provider_id='history-test', caller_kind='direct')
    client.portal.call(publish, second.run_id, {**base, 'revision': 3, 'nodes': [
        {'id': 'verify', 'label': 'Verify parser', 'status': 'running'}]})
    expected = ['failed-attempt', 'retry', 'verify']
    assert [node['id'] for node in read(client, graph, agent).json()['nodes']] == expected
    # Read the persisted store from disk through a fresh instance, not a view cache.
    database = Database(store.database.path)
    try:
        persisted = RunStore(database).get(second.run_id).lifecycle['execution_graph']
    finally:
        database.close()
    assert [node['id'] for node in persisted['nodes']] == expected
    assert persisted['nodes'][0]['status'] == 'failed'
    assert persisted['edges'][0]['source'] == 'failed-attempt'
    conversation = create_node(client, 'conversation')
    session = client.get('/api/conversations/' + conversation['id']).json()['sessions'][0]['id']
    third = store.create(agent_id=agent['id'], runtime_provider_id='history-test', caller_kind='conversation',
                         caller_id=conversation['id'], context_id=session)
    client.portal.call(publish, third.run_id, {**base, 'revision': 4, 'nodes': [{'id': 'private', 'label': 'Other project'}]})
    assert [node['id'] for node in read(client, graph, agent, session).json()['nodes']] == ['private']
    assert [node['id'] for node in read(client, graph, agent).json()['nodes']] == expected
