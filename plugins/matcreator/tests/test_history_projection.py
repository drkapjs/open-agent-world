"""Actual Pack history schema projected through the public host validator."""
import json

from open_agent_world.plugin_api import ExecutionSnapshot
from plugins.matcreator.oaw_matcreator.native_projection import project_history


def project(nodes, edges=(), plans=None):
    result = project_history({'history_id': 'session-scope', 'history': {
        'schema_version': 2, 'revision': 7, 'nodes': {node['id']: node for node in nodes},
        'edges': list(edges), 'plans': plans or {},
    }})
    return ExecutionSnapshot.model_validate(result)


def execution(number, **extra):
    return {'id': f'execution_{number}', 'type': 'execution', 'label': f'E{number:02}',
        'status': 'success', 'sequence': number, 'start_time': f'2026-10-13T00:{number % 60:02}:00Z',
        'parent_id': 'planning', **extra}


def test_twenty_sequential_executions_do_not_imply_dependencies():
    value = project([execution(i) for i in range(1, 21)])
    assert len(value.nodes) == 20
    assert value.edges == []
    assert all(node.execution_id == node.id for node in value.nodes)
    assert value.nodes[-1].sequence == 20
    assert value.nodes[-1].started_at == '2026-10-13T00:20:00Z'
    assert value.history_id == 'session-scope'


def test_sparse_cross_execution_data_control_and_artifact_fan_in_are_distinct():
    nodes = [execution(i) for i in range(1, 21)] + [
        {'id': f'step_{i}', 'type': 'step', 'execution_id': f'execution_{i}',
         'parent_id': f'execution_{i}', 'dependency_ids': ['step_1'] if i == 20 else [],
         'artifacts': [f'oaw:artifact:outputs:v{i}']} for i in [1, 9, 17, 20]]
    edges = [{'from': f'step_{i}', 'to': 'step_20', 'kind': 'data',
        'artifact_ref': f'oaw:artifact:outputs:v{i}', 'evidence': 'artifact_inspected'} for i in [1, 9, 17]]
    edges += [{'from': 'step_1', 'to': 'step_20', 'kind': 'control', 'evidence': 'scheduler_predecessor'}]
    value = project(nodes, edges)
    assert len(value.edges) == 4
    assert {edge.source for edge in value.edges} == {'agent:step_1', 'agent:step_9', 'agent:step_17'}
    assert all(edge.target == 'agent:step_20' for edge in value.edges)
    assert value.nodes[-1].execution_id == 'agent:execution_20'
    assert {edge.artifact_ref for edge in value.edges if edge.kind == 'data'} == {
        'oaw:artifact:outputs:v1', 'oaw:artifact:outputs:v9', 'oaw:artifact:outputs:v17'}
    assert next(edge for edge in value.edges if edge.kind == 'control').evidence == 'scheduler_predecessor'


def test_plan_identity_is_explicit_and_retry_never_retargets_old_edges():
    value = project([
        {'id': 'a1', 'type': 'step', 'status': 'failed', 'plan_id': 'p1', 'logical_node_id': 'same'},
        {'id': 'b1', 'type': 'step', 'plan_id': 'p1', 'dependency_ids': ['a1']},
        {'id': 'a2', 'type': 'step', 'plan_id': 'p1', 'logical_node_id': 'same', 'retry_of': 'a1', 'attempt': 2},
    ], plans={
        'p1': {'nodes': {'same': {'attempt_id': 'a2'}, 'executed': {'attempt_id': 'b1'}, 'later': {}},
               'edges': [['same', 'executed'], ['same', 'later']]},
        'p2': {'nodes': {'same': {}}, 'edges': []},
    })
    assert {node.id for node in value.nodes} == {'agent:a1', 'agent:a2', 'agent:b1',
        'plan:p1:same', 'plan:p1:executed', 'plan:p1:later', 'plan:p2:same'}
    assert next(node for node in value.nodes if node.id == 'plan:p1:same').realized_by == 'agent:a2'
    assert next(node for node in value.nodes if node.id == 'agent:a1').workstream_id == next(
        node for node in value.nodes if node.id == 'agent:a2').workstream_id
    assert {(edge.source, edge.target, edge.kind) for edge in value.edges} == {
        ('agent:a1', 'agent:b1', 'control'), ('agent:a1', 'agent:a2', 'retry'),
        ('agent:a2', 'plan:p1:later', 'control')}
    assert next(node for node in value.nodes if node.id == 'agent:a1').status == 'failed'


def test_large_history_keeps_every_execution_and_step_including_latest():
    nodes = []
    for number in range(120):
        nodes.append(execution(number))
        nodes.extend({'id': f'step_{number}_{i}', 'type': 'step',
            'execution_id': f'execution_{number}', 'status': 'success'} for i in range(9))
    value = project(nodes)
    assert len(value.nodes) == value.total_nodes == 1200
    assert value.truncated is False
    assert value.nodes[-1].id == 'agent:step_119_8'


def test_legacy_parent_links_are_not_migrated_as_dependencies_or_private_traces():
    value = project([
        {'id': 'a', 'type': 'planning', 'input': {'private': 'secret'}, 'conversation': 'secret'},
        {'id': 'b', 'type': 'execution', 'parent_id': 'a', 'tool_calls': ['secret']},
    ], [{'from': 'a', 'to': 'b'}])
    assert value.edges == []
    assert 'secret' not in value.model_dump_json()


def test_transport_limits_are_explicit_and_keep_current_work():
    nodes = [execution(i) for i in range(10005)]
    nodes[0]['status'] = 'running'
    value = project(nodes)
    assert len(value.nodes) == 10000
    assert value.total_nodes == 10005 and value.truncated
    assert {'agent:execution_0', 'agent:execution_10004'} <= {node.id for node in value.nodes}
    assert len(json.dumps(value.model_dump()).encode()) < 8 * 1024 * 1024


def test_size_budget_includes_host_defaults_for_sparse_plan_nodes():
    plans = {f'p{i}': {'nodes': {f'n{j}': {'action': 'x' * 550} for j in range(20)}} for i in range(500)}
    value = project([], plans=plans)
    assert len(value.nodes) == 10000
    assert value.truncated
    assert len(json.dumps(value.model_dump(), ensure_ascii=False).encode()) < 8 * 1024 * 1024


def test_recovered_target_retains_declared_predecessor_with_unknown_attempt():
    value = project([{'id': 'actual_b', 'type': 'step', 'plan_id': 'p'}], plans={
        'p': {'nodes': {'a': {'status': 'success'}, 'b': {'attempt_id': 'actual_b'}}, 'edges': [['a', 'b']]},
    })
    assert len(value.edges) == 1
    edge = value.edges[0]
    assert (edge.source, edge.target) == ('plan:p:a', 'agent:actual_b')
    assert edge.evidence == 'declared_plan_control_unresolved_attempt'


def test_operations_have_stable_task_ownership_and_tools_do_not_expose_private_arguments():
    value = project([
        {'id': 'orchestrator', 'type': 'orchestrator'},
        {'id': 'p', 'type': 'planning'},
        {'id': 'exec', 'type': 'execution'},
        {'id': 'step', 'type': 'step', 'label': 'Node n1', 'plan_id': 'plan', 'logical_node_id': 'n1',
         'input': {'action': 'Repair parser', 'prior_context': 'PRIVATE'},
         'tool_calls': [{'name': 'write_file', 'input': {'secret': 'PRIVATE'}, 'output': {'result': 'PRIVATE'},
                         'start_time': '2026-10-13T10:00:00Z', 'end_time': '2026-10-13T10:00:01Z'}]},
    ])
    by_id = {node.id: node for node in value.nodes}
    assert value.retention == 'history'
    assert by_id['agent:orchestrator'].record_type == by_id['agent:exec'].record_type == 'context'
    assert by_id['agent:p'].record_type == 'activity'
    assert by_id['agent:step'].label == 'Repair parser'
    tool = by_id['agent:step:tool:0']
    assert tool.workstream_id == by_id['agent:step'].workstream_id
    assert tool.actor_id == by_id['agent:step'].actor_id
    assert tool.label == 'write_file'
    assert value.edges == []  # Ownership does not invent data/control edges.
    assert 'PRIVATE' not in value.model_dump_json()


def test_recursive_local_step_ids_do_not_share_the_scheduled_tasks_rail():
    value = project([
        {'id': 'parent', 'type': 'step', 'plan_id': 'p', 'logical_node_id': 'n1'},
        {'id': 'child', 'type': 'step', 'plan_id': 'p', 'logical_node_id': 'n1', 'parent_id': 'parent'},
    ])
    assert value.nodes[0].workstream_id != value.nodes[1].workstream_id
