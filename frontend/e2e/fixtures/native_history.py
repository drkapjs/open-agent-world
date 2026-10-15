"""Exercise the real Pack adapter/projection with a small native-format history.

No model or service is involved. This catches fixture-only UI improvements that
would disappear when the worker's public-field filtering is applied.
"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from plugins.matcreator.oaw_matcreator.native_adapter import _public_node
from plugins.matcreator.oaw_matcreator.native_projection import project_history

records = [
    {'id': 'orchestrator', 'type': 'orchestrator', 'label': 'Orchestrator'},
    {'id': 'p1', 'type': 'planning', 'label': 'Plan structure analysis', 'status': 'failed'},
    {'id': 'p2', 'type': 'planning', 'label': 'Revise analysis plan', 'retry_of': 'p1'},
    {'id': 'execution_1', 'type': 'execution', 'label': 'Execution 1', 'parent_id': 'p2'},
]
labels = ['Prepare environment', 'Generate samples', 'Analyze statistics', 'Check integrity', 'Write report']
for i, action in enumerate(labels, 1):
    records.append({'id': f's{i}', 'type': 'step', 'label': f'Node n{i}', 'plan_id': 'plan',
        'execution_id': 'execution_1', 'parent_id': 'execution_1', 'logical_node_id': f'n{i}',
        'input': {'action': action, 'prior_context': 'private fixture content'},
        'dependency_ids': {1: [], 2: ['s1'], 3: ['s2'], 4: ['s2'], 5: ['s3', 's4']}[i]})
records.append({'id': 'p3', 'type': 'planning', 'label': 'Review generated report'})
for i, record in enumerate(records):
    record.setdefault('status', 'success')
    record['sequence'] = i
    record['start_time'] = f'2026-10-13T08:{i:02}:00Z'
    record['end_time'] = f'2026-10-13T08:{i:02}:30Z'
records[4]['tool_calls'] = [{'name': 'Check runtime', 'start_time': '2026-10-13T08:04:10Z',
    'end_time': '2026-10-13T08:04:20Z', 'input': {'secret': 'private fixture content'}, 'output': {'status': 'success'}}]
value = project_history({'goal': 'Structure study', 'phase': 'idle', 'history_id': 'native-fixture', 'history': {
    'revision': 1, 'nodes': {record['id']: _public_node(record) for record in records},
    'edges': [{'from': 'p2', 'to': 's1', 'kind': 'fork', 'evidence': 'task_launched'}],
}})
assert 'private fixture content' not in json.dumps(value)
print(json.dumps(value))
