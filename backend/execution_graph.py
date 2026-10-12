"""Connected, session-scoped execution views. Execution remains owned by Runs."""
from backend.errors import PermissionDeniedError
from backend.plugins.execution_graph import ExecutionSnapshot


def _projection(run):
    value = ExecutionSnapshot.model_validate(run.lifecycle['execution_graph'])
    value.run_id = run.run_id
    value.previous_run_id = ''
    # A killed/recovered process cannot leave the display running forever.
    if run.status.value in {'succeeded', 'failed', 'cancelled', 'interrupted'}:
        value.phase = run.status.value
        for node in value.nodes:
            if node.status in {'running', 'pending', 'waiting', 'created'}:
                node.status = run.status.value
    return value


def connected_sources(services, viewer_id):
    viewer = services.world.get_card(viewer_id)
    if not services.plugins.has_trait(viewer.type, 'core.execution-viewer'):
        raise PermissionDeniedError('This card is not an execution viewer')
    sources = {}
    for edge in services.world.list_edges_from(viewer_id):
        if edge.missing_plugin or edge.relationship != 'core.execution-source' or edge.direction not in {'forward', 'bidirectional'}:
            continue
        node = services.world.get_card(edge.target)
        if services.plugins.has_trait(node.type, 'core.execution-source'):
            sources[node.id] = {'id': node.id, 'name': node.name, 'type': node.type}
    return list(sources.values())


def snapshot(services, source_id, context):
    source = services.world.get_card(source_id)
    definition = services.plugins.node_type(source.type)
    spec = definition.execution_source
    if spec and spec.read:
        return ExecutionSnapshot.model_validate(spec.read(context)).model_dump(mode='json')
    manager = services.run_manager
    session = services.card_state.session_id(source_id)
    # Summons can have a private context ID. Their conversation belongs to the
    # durable Run ancestry, not to that private reasoning context.
    def in_session(run):
        return (manager._conversation_scope(run)[1] or 'default') == session
    if 'core.agent' in definition.traits:
        own = [run for run in manager.list_runs(agent_id=source_id) if in_session(run)]
        if not own:
            return ExecutionSnapshot().model_dump()
        # A concurrent send may create a rejected Run while the original worker
        # is still active. That bookkeeping must not replace the live graph.
        active = [run for run in own if run.status.value in {'running', 'waiting'}]
        current = active[-1] if active else own[-1]
        projected = current.lifecycle.get('execution_graph')
        if projected:
            return _projection(current).model_dump(mode='json')
        previous = next((run for run in reversed(own[:own.index(current)])
                         if run.runtime_provider_id == current.runtime_provider_id
                         and run.lifecycle.get('execution_graph', {}).get('nodes')), None)
        if previous:
            value = _projection(previous)
            value.previous_run_id = previous.run_id
            value.run_id = current.run_id
            value.phase = current.status.value
            return value.model_dump(mode='json')
        runs = [current]
        for parent in runs:
            if len(runs) > 500:
                break
            runs.extend(run for run in manager.store.list_children(parent.run_id) if in_session(run))
    else:
        libraries = {source_id} if definition.summoning else set()
        if spec and spec.kind == 'summoning-adapter':
            for edge in services.world.list_edges_from(source_id):
                if edge.missing_plugin or edge.direction not in {'forward', 'bidirectional'}:
                    continue
                target = services.world.get_card(edge.target)
                summon = services.plugins.node_type(target.type).summoning
                relation = services.plugins.relationship(edge.relationship)
                if summon and any(grant.kind == summon.capability_kind for grant in relation.capabilities):
                    libraries.add(target.id)
        records = [record for record in services.summoning.records() if record['library_id'] in libraries
                   and (record.get('session_id') or 'default') == session]
        # Retain separate Run nodes for follow-up turns; retries never overwrite history.
        runs = [manager.get_run(attempt['run_id']) for record in records for attempt in record['attempts']]
        runs = [run for run in runs if in_session(run)]
    nodes = []
    for run in (runs[:500] if 'core.agent' in definition.traits else runs[-500:]):
        actor = services.world.maybe_get_card(run.agent_id)
        nodes.append({'id': run.run_id, 'label': actor.name if actor else 'Reclaimed Agent', 'kind': 'agent',
                      'status': run.status.value, 'parent_id': run.parent_run_id,
                      'detail': run.error or (f'Task: {run.task_id}' if run.task_id else ''),
                      'result': manager.final_text(run.run_id)[:20000]})
    phase = 'running' if any(node['status'] in {'running', 'created', 'waiting'} for node in nodes) else nodes[-1]['status'] if nodes else 'idle'
    return ExecutionSnapshot(run_id=current.run_id if 'core.agent' in definition.traits else '',
        title=source.name, phase=phase, nodes=nodes, truncated=len(runs) > 500).model_dump(mode='json')


def register(registration):
    from pydantic import BaseModel, ConfigDict
    from backend.plugins.registry import NodeTypeDefinition, RelationshipDefinition
    from backend.plugins.state import ScopedStateSpec
    from backend.plugins.registry import NodePresentation, WorkspaceLayoutMetadata

    class Config(BaseModel):
        model_config = ConfigDict(extra='forbid')

    registration.register_node_type(NodeTypeDefinition(
        id='core.execution-graph', label='Agent graph', default_name='Agent graph',
        description='Observe connected Agents, callable libraries and custom execution sources.',
        icon='workflow', color='#879a9d', deck_id='tools', deck_label='Tools', deck_icon='boxes',
        default_size=(680, 460), default_status='available', statuses=frozenset({'available'}), config_model=Config,
        traits=frozenset({'core.execution-viewer'}), templateable=True,
        state=ScopedStateSpec(supported_scopes=('session',), default_scope='session'),
        presentation=NodePresentation(states=('node', 'preview', 'inspector', 'workspace'), initial='preview', open='workspace',
            workspace_layout=WorkspaceLayoutMetadata(min_width=210, min_height=280, ideal_width=300, ideal_height=640)),
    ))
    registration.register_relationship(RelationshipDefinition(
        id='core.execution-source', label='Observe execution', short_label='execution',
        description='Read execution state from this source in the selected session. Grants no execution or control permissions.',
        source_traits=frozenset({'core.execution-viewer'}), target_traits=frozenset({'core.execution-source'}), templateable=True,
    ))
