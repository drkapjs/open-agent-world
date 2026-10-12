import type { ExecutionSnapshot } from '../plugins/executionSources';
export type GraphNode = { id: string; label: string; status: string; badge: string; detail?: string; result?: string; x: number; y: number };
export type GraphEdge = { source: string; target: string; dependency: boolean };

/** Stable layered layout, including partial snapshots and damaged cycles. */
export function executionGraph(snapshot: ExecutionSnapshot | undefined, mode: 'agents' | 'plan') {
  const unique = [...new Map((snapshot?.nodes ?? []).filter(node => mode === 'plan' ? node.kind === 'plan' : node.kind !== 'plan')
    .map(node => [node.id, { ...node, badge: node.kind }])).values()];
  const ids = new Set(unique.map(node => node.id));
  const links: GraphEdge[] = unique.flatMap(node => [
    ...(node.parent_id ? [{ source: node.parent_id, target: node.id, dependency: false }] : []),
    ...(node.dependency_ids ?? []).map(source => ({ source, target: node.id, dependency: true })),
  ]);
  const edges = [...new Map(links.filter(edge => ids.has(edge.source) && ids.has(edge.target) && edge.source !== edge.target)
    .map(edge => [`${edge.source}\0${edge.target}\0${edge.dependency}`, edge])).values()];
  const levels = new Map(unique.map(node => [node.id, 0]));
  const incoming = new Map(unique.map(node => [node.id, 0]));
  edges.forEach(edge => incoming.set(edge.target, incoming.get(edge.target)! + 1));
  const queue = unique.filter(node => !incoming.get(node.id)).map(node => node.id);
  for (let index = 0; index < queue.length; index++) {
    const id = queue[index];
    for (const edge of edges.filter(edge => edge.source === id)) {
      levels.set(edge.target, Math.max(levels.get(edge.target)!, levels.get(id)! + 1));
      incoming.set(edge.target, incoming.get(edge.target)! - 1);
      if (!incoming.get(edge.target)) queue.push(edge.target);
    }
  }
  const layers = new Map<number, typeof unique>();
  unique.forEach(node => { const level = levels.get(node.id)!; layers.set(level, [...layers.get(level) ?? [], node]); });
  const width = Math.max(200, ...[...layers.values()].map(layer => layer.length * 154 + 24));
  const nodes: GraphNode[] = [...layers].flatMap(([level, layer]) => layer.map((node, index) => ({ ...node,
    x: width / 2 + (index - (layer.length - 1) / 2) * 154, y: 48 + level * 126,
  })));
  return { nodes, edges, width, height: Math.max(180, 130 + Math.max(0, ...levels.values()) * 126) };
}
