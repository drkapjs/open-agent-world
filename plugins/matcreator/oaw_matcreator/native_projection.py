"""Translate Pack history into the host's read-only, typed execution contract.

Containment and chronological order never create dependency edges. Plan
definitions map to an actual attempt only through the engine's explicit ID.
"""
from __future__ import annotations

import hashlib
import json

from open_agent_world.plugin_api import ExecutionSnapshot


def _agent(key):
    return 'agent:' + str(key) if key else None


def project_history(value):
    history = value.get('history') or {}
    records = history.get('nodes', value.get('agents', []))
    if isinstance(records, dict):
        records = list(records.values())
    by_id = {record['id']: record for record in records}
    nodes = []
    edges = {}

    def edge(source, target, kind='control', *, evidence=None, artifact_ref=None):
        if not source or not target or source == target:
            return
        key = (source, target, kind, artifact_ref)
        # One relationship can be reported both in dependency_ids and edges.
        # Prefer the explicit evidence over the legacy control shorthand.
        identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()[:32]
        record = {'id': 'edge:' + identity, 'source': source, 'target': target, 'kind': kind}
        if evidence:
            record['evidence'] = str(evidence)[:2000]
        if artifact_ref:
            record['artifact_ref'] = artifact_ref
        if key not in edges or evidence:
            edges[key] = record

    for raw in records:
        identity = _agent(raw['id'])
        role = raw.get('type') or ''
        task_input = raw.get('input') if isinstance(raw.get('input'), dict) else {}
        logical = raw.get('logical_node_id') or task_input.get('node_id')
        stream = (str(raw.get('plan_id')) + ':' + str(logical)) if raw.get('plan_id') and logical else str(raw['id'])
        if logical and by_id.get(raw.get('parent_id'), {}).get('type') == 'step':
            stream = str(raw['parent_id']) + ':subtask:' + str(logical)
        if role == 'planning':
            stream = 'planning'
        stream = raw.get('workstream_id') or stream
        action = str(raw.get('action') or task_input.get('action') or '')
        label = str(raw.get('label') or raw['id'])
        if role == 'step' and action and label.startswith('Node '):
            label = action.splitlines()[0][:120]
        actor_id = raw.get('actor_id')
        actor_label = str(raw.get('actor_label') or (('Executor ' + str(logical)) if role == 'step' and logical
            else 'Executor' if role == 'step' else 'Planner' if role == 'planning' else role.title()))
        node = {
            'id': identity, 'label': label[:500],
            'kind': 'tool' if role == 'tool' else 'agent', 'role': role,
            'status': raw.get('status') or 'pending', 'parent_id': _agent(raw.get('parent_id')),
            'execution_id': _agent(raw['id'] if role == 'execution' else raw.get('execution_id')),
            'plan_id': raw.get('plan_id'), 'sequence': raw.get('sequence'),
            'started_at': raw.get('start_time'), 'finished_at': raw.get('end_time'),
            'attempt': raw.get('attempt'), 'retry_of': _agent(raw.get('retry_of')),
            'replan_of': _agent(raw.get('replan_of')),
            'actor_id': str(actor_id)[:512] if actor_id else None, 'actor_label': actor_label[:500],
            'workstream_id': stream[:512], 'workstream_label': label[:500],
            'record_type': 'context' if role in {'orchestrator', 'execution'} else 'activity',
            'operation': 'plan' if role == 'planning' else 'run',
            'artifact_refs': list(dict.fromkeys([*(raw.get('artifacts') or []),
                *(raw.get('produced_artifacts') or [])]))[:1000],
            'detail': str(raw.get('summary') or action or '')[:20000],
        }
        nodes.append(node)
        for dependency in raw.get('dependency_ids') or []:
            edge(_agent(dependency), identity, evidence='declared_control')
        for kind in ('retry', 'replan'):
            edge(node[kind + '_of'], identity, kind, evidence='recorded_attempt')
        # Tools are durable evidence inside the owning task event. The viewer
        # groups them into that event instead of creating a vertex per call.
        # Publish only operation names/times; private arguments, conversations
        # and raw tool responses stay in the provider's native trace.
        for number, call in enumerate(raw.get('tool_calls') or []):
            if not isinstance(call, dict) or not call.get('name'):
                continue
            output = call.get('output') if isinstance(call.get('output'), dict) else {}
            status = call.get('status') or ('failed' if output.get('error') or output.get('status') in ('error', 'failed') else 'completed')
            nodes.append({
                'id': identity + ':tool:' + str(call.get('ordinal', number)), 'label': str(call['name'])[:500],
                'kind': 'tool', 'role': 'tool', 'operation': 'tool', 'status': status,
                'parent_id': identity, 'execution_id': node['execution_id'],
                'actor_id': node['actor_id'], 'actor_label': node['actor_label'],
                'workstream_id': node['workstream_id'], 'workstream_label': node['workstream_label'],
                'started_at': call.get('start_time'), 'finished_at': call.get('end_time'),
            })
    for raw in history.get('edges', []):
        kind = raw.get('kind')
        # Old untyped edges included parent calls. Only dependency_ids above
        # can establish control relationships for legacy history.
        if kind in ('control', 'data', 'retry', 'replan', 'fork', 'merge'):
            edge(_agent(raw.get('from')), _agent(raw.get('to')), kind,
                 evidence=raw.get('evidence'), artifact_ref=raw.get('artifact_ref'))

    plans = dict(history.get('plans') or {})
    graph = value.get('graph') or {}
    if graph.get('nodes'):
        plans.setdefault(graph.get('graph_id') or '', graph)
    actual_ids = {node['id'] for node in nodes}
    for plan_id, plan in plans.items():
        definitions = plan.get('nodes') or {}
        ids = {}
        realized = set()
        for key, raw in definitions.items():
            attempt = _agent(raw.get('attempt_id'))
            planned_id = 'plan:' + (str(plan_id) + ':' if plan_id else '') + key
            if attempt and attempt in actual_ids:
                ids[key] = attempt
                realized.add(key)
            else:
                ids[key] = planned_id
            # A plan entry remains addressable after execution begins. It is
            # supporting context, never a moving placeholder in the history.
            nodes.append({
                    'id': planned_id, 'label': str(raw.get('label') or key)[:500],
                    'kind': 'plan', 'role': 'planned_step', 'status': raw.get('status') or 'pending',
                    'record_type': 'planned', 'realized_by': attempt if attempt in actual_ids else None,
                    'plan_id': plan_id or None, 'parent_id': _agent(plan.get('planning_id')),
                    'detail': str(raw.get('action') or '')[:20000],
                    'result': str(raw.get('result') or '')[:20000],
                })
        for pair in plan.get('edges', []):
            if len(pair) != 2:
                continue
            source, target = pair
            # Executed attempts have their own recorded scheduler edges. Never
            # retarget a historical attempt when its predecessor is retried.
            if source in ids and target in ids:
                if target not in realized:
                    edge(ids[source], ids[target], evidence='declared_plan_control')
                elif source not in realized:
                    # Recovery/legacy data may know the declared prerequisite
                    # but have no attempt identity. Keep that plan reference;
                    # never substitute another similarly named actual Step.
                    edge(ids[source], ids[target], evidence='declared_plan_control_unresolved_attempt')
    result = {
        'phase': value.get('phase') or 'idle', 'title': str(value.get('goal') or '')[:2000],
        'history_id': str(value.get('history_id') or history.get('history_id') or ''),
        'revision': history.get('revision') or 0, 'retention': 'history', 'nodes': nodes, 'edges': list(edges.values()),
        'total_nodes': len(nodes), 'truncated': bool(history.get('detail_omitted') or history.get('truncated')),
    }
    return _bounded(result)


def _bounded(snapshot):
    """Normal histories remain complete; extraordinary histories declare limits.

    Keep active work and recent attempts if the generic host transport limit is
    exceeded. Edges adjacent to retained nodes still name omitted endpoints.
    """
    nodes, edges = snapshot['nodes'], snapshot['edges']
    active = {'running', 'failed', 'needs_replanning', 'waiting', 'interrupted'}
    priority = sorted(range(len(nodes)), key=lambda i: (nodes[i].get('status') in active, i), reverse=True)
    if len(nodes) > 10000:
        keep = set(priority[:10000])
        nodes = [node for i, node in enumerate(nodes) if i in keep]
        snapshot['truncated'] = True
    if len(edges) > 50000:
        edges = edges[-50000:]
        snapshot['truncated'] = True
    snapshot.update(nodes=nodes, edges=edges)
    # Measure the same default-filled representation persisted by the host.
    # Sparse plan nodes otherwise underestimate size by several MiB at 10k.
    def size():
        return len(json.dumps(ExecutionSnapshot.model_validate(snapshot).model_dump(mode='json'), ensure_ascii=False).encode())

    budget = 8 * 1024 * 1024 - 4096  # Room for the host's Run identity.
    if size() > budget:
        for node in nodes:
            for field in ('detail', 'result'):
                if len(node.get(field, '')) > 256:
                    node[field] = node[field][:256] + '…'
        snapshot['truncated'] = True
    while size() > budget:
        if len(nodes) > 1:
            keep = {node['id'] for node in sorted(nodes,
                key=lambda node: node.get('status') in active)[-max(1, len(nodes) * 3 // 4):]}
            nodes = [node for node in nodes if node['id'] in keep]
            edges = [edge for edge in edges if edge['source'] in keep or edge['target'] in keep]
        else:
            edges = edges[len(edges) // 4 + 1:]
            if not edges:
                nodes = []
        snapshot.update(nodes=nodes, edges=edges, truncated=True)
    return snapshot
