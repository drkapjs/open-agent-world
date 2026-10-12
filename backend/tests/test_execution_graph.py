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
    assert [n['status'] for n in value['nodes']] == ['succeeded', 'succeeded']


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
