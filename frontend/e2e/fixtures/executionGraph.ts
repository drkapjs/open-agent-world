import type { ExecutionEdge, ExecutionNode, ExecutionSnapshot } from '../../src/plugins/executionSources';

const edge = (source: string, target: string, kind: ExecutionEdge['kind'] = 'control'): ExecutionEdge => ({ id: `${source}:${target}:${kind}`, source, target, kind });
export function graphFixture(scenario: string): ExecutionSnapshot {
  const snapshot: ExecutionSnapshot = { title: 'Structure search · research session', phase: 'idle', history_id: scenario, revision: 1, nodes: [], edges: [] };
  if (scenario === 'empty') return snapshot;
  if (scenario === 'independent') {
    snapshot.title = 'Three independent executions';
    snapshot.nodes = ['Prepare structure', 'Check symmetry', 'Export results'].map((label, i) => ({ id: `n${i}`, label, kind: 'agent', role: 'execution', status: i === 2 ? 'running' : 'success', sequence: i }));
    return snapshot;
  }
  if (scenario === 'parallel') {
    snapshot.title = 'Crystal parser · repair and verify';
    const operations = [
      ['project', 'Review parser failure', 'Mira', 'plan'],
      ['repair', 'Update structure parser', 'Atlas', 'edit'],
      ['verify', 'Add regression cases', 'Nova', 'edit'],
      ['repair', 'Handle missing coordinates', 'Atlas', 'edit'],
      ['verify', 'Run parser tests', 'Nova', 'run'],
      ['repair', 'Fix empty structure input', 'Atlas', 'edit'],
      ['verify', 'Verify the repair', 'Nova', 'run'],
      ['project', 'Apply verified changes', 'Mira', 'merge'],
    ];
    snapshot.nodes = operations.map(([stream, label, actor, operation], i) => ({ id: `E${i + 1}`, label, kind: 'agent',
      role: 'step', actor_id: actor, actor_label: actor, workstream_id: stream, operation,
      status: i === 4 ? 'failed' : 'success', sequence: i,
      started_at: new Date(Date.UTC(2026, 9, 13, 9, 10 + i)).toISOString(),
      detail: i === 4 ? 'Empty structure input failed validation. The failed attempt remains in the project history.' : '',
    }));
    snapshot.edges = [edge('E1','E2','fork'), edge('E1','E3','fork'), edge('E4','E5','data'),
      edge('E5','E6','data'), edge('E5','E7','retry'), edge('E6','E7','data'), edge('E6','E8','merge'), edge('E7','E8','merge')];
    return snapshot;
  }
  const count = scenario === 'large' ? 120 : 20;
  const nodes: ExecutionNode[] = [];
  for (let n = 1; n <= count; n++) {
    const id = `E${String(n).padStart(2, '0')}`;
    nodes.push({ id, label: id, kind: 'agent', role: 'execution', execution_id: id, sequence: n * 10,
      actor_id: scenario === 'large' ? `executor-${n % 3}` : undefined, actor_label: scenario === 'large' ? ['Atlas', 'Nova', 'Mira'][n % 3] : undefined,
      workstream_id: scenario === 'large' ? id : undefined, status: n === count ? 'running' : 'success' });
    if (scenario === 'large') for (let step = 1; step <= 9; step++) {
      nodes.push({ id: `${id}:s${step}`, label: ['Prepare inputs', 'Relax geometry', 'Check symmetry', 'Evaluate energy', 'Compare structures', 'Compute transport', 'Validate outputs', 'Review results', 'Publish result'][step - 1], kind: 'plan', role: 'step', parent_id: id, execution_id: id,
        actor_id: `executor-${n % 3}`, actor_label: ['Atlas', 'Nova', 'Mira'][n % 3], workstream_id: id,
        sequence: n * 10 + step, status: n === count ? step < 6 ? 'success' : step === 9 ? 'waiting' : 'running' : 'success' });
      if (step > 1) snapshot.edges!.push(edge(`${id}:s${step === 5 ? 2 : step - 1}`, `${id}:s${step}`));
    }
  }
  snapshot.nodes = nodes;
  if (scenario === 'sparse') snapshot.edges = [1,9,17].map(n => edge(`E${String(n).padStart(2, '0')}`, 'E20', 'data'));
  else {
    for (let n = 1; n < 120; n++) snapshot.edges!.push(edge(`E${String(n).padStart(2, '0')}:s9`, `E${String(n + 1).padStart(2, '0')}:s1`));
    snapshot.edges!.push(...[1,9,17].map(n => edge(`E${String(n).padStart(2, '0')}:s9`, 'E120:s9', 'data')));
  }
  return snapshot;
}
