import type { ExecutionEdge, ExecutionNode, ExecutionSnapshot } from '../plugins/executionSources';

export const GRAPH_ROW_HEIGHT = 54;
export const GRAPH_LANES = 4;
export const isComplete = (status: string) => ['success', 'succeeded', 'completed'].includes(status);
export const isContext = (node: ExecutionNode) => node.record_type === 'context';
export const isPlanned = (node: ExecutionNode) => node.record_type === 'planned' || node.role === 'planned_step';
export type ExecutionIndex = ReturnType<typeof executionIndex>;
export type LaneMemory = { nodes: Map<string, number>; streams: Map<string, number>; actors: Map<string, number> };
export const laneMemory = (): LaneMemory => ({ nodes: new Map(), streams: new Map(), actors: new Map() });

/** Global chronology. Containers never reorder activities. Ordering and
 * containment never manufacture causal edges. */
export function executionIndex(snapshot?: ExecutionSnapshot) {
  const nodes = [...new Map((snapshot?.nodes ?? []).map(node => [node.id, node])).values()];
  const byId = new Map(nodes.map(node => [node.id, node]));
  const edges: ExecutionEdge[] = [...new Map((snapshot?.edges ?? nodes.flatMap(node => (node.dependency_ids ?? []).map(source => ({
    id: 'control:' + source + ':' + node.id, source, target: node.id, kind: 'control' as const,
  })))).map(edge => [edge.id, edge])).values()];
  const incoming = new Map<string, ExecutionEdge[]>(), outgoing = new Map<string, ExecutionEdge[]>();
  for (const edge of edges) {
    if (!incoming.has(edge.target)) incoming.set(edge.target, []);
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, []);
    incoming.get(edge.target)!.push(edge); outgoing.get(edge.source)!.push(edge);
  }
  const ordinal = new Map(nodes.map((node, i) => [node.id, i]));
  const hasTimes = nodes.some(node => Number.isFinite(Date.parse(node.started_at ?? '')));
  let lastTime = Date.parse(nodes.find(node => Number.isFinite(Date.parse(node.started_at ?? '')))?.started_at ?? '') || 0;
  const order = new Map(nodes.map(node => {
    const time = Date.parse(node.started_at ?? '');
    if (Number.isFinite(time)) lastTime = time;
    return [node.id, hasTimes ? lastTime : node.sequence ?? ordinal.get(node.id)!];
  }));
  const ordered = nodes.slice().sort((a, b) => {
    return order.get(a.id)! - order.get(b.id)!
      || (a.sequence ?? ordinal.get(a.id)!) - (b.sequence ?? ordinal.get(b.id)!);
  });
  const activities = ordered.filter(node => !isPlanned(node) && (!isContext(node)
    || incoming.has(node.id) || outgoing.has(node.id)));
  return { nodes, byId, edges, incoming, outgoing, ordered, activities };
}
export const nodeEdges = (index: ExecutionIndex, id?: string) => id
  ? [...new Map([...(index.incoming.get(id) ?? []), ...(index.outgoing.get(id) ?? [])].map(edge => [edge.id, edge])).values()] : [];

export type PositionedRow = { id: string; node: ExecutionNode; top: number; y: number; height: number; lane: number; color: number; stream: string };
export type GraphTrack = { id: string; source: PositionedRow; target: PositionedRow; edge?: ExecutionEdge;
  kind: ExecutionEdge['kind'] | 'history' | 'workstream'; color: number; reference: boolean };

/** Remember allocation by identity, independent of status, selection and hover.
 * Four columns bound the surface; overflow references identify real endpoints. */
export function layoutGraph(index: ExecutionIndex, memory: LaneMemory, selected?: string, detailHeight = 0, query = '') {
  const positions = new Map(index.ordered.map((node, i) => [node.id, i]));
  const lastConsumer = new Map<string, number>();
  for (const edge of index.edges) if (positions.has(edge.target)) lastConsumer.set(edge.source,
    Math.max(lastConsumer.get(edge.source) ?? 0, positions.get(edge.target)!));
  const streamNodes = new Map<string, ExecutionNode[]>();
  for (const node of index.activities) if (node.workstream_id) {
    const list = streamNodes.get(node.workstream_id) ?? []; list.push(node); streamNodes.set(node.workstream_id, list);
  }
  const occupied = new Map<number, number>();
  for (const node of index.activities) {
    const position = positions.get(node.id)!;
    for (const [lane, end] of occupied) if (end <= position) occupied.delete(lane);
    if (!memory.nodes.has(node.id)) {
      const parents = (index.incoming.get(node.id) ?? []).map(edge => edge.source);
      const inherited = parents.map(id => memory.nodes.get(id)).find(lane => lane !== undefined && !occupied.has(lane));
      const streamLane = node.workstream_id ? memory.streams.get(node.workstream_id) : undefined;
      const available = [0, 1, 2, 3].find(lane => !occupied.has(lane));
      const lane = streamLane ?? inherited ?? available ?? position % GRAPH_LANES;
      memory.nodes.set(node.id, lane);
      if (node.workstream_id) memory.streams.set(node.workstream_id, lane);
    }
    const lane = memory.nodes.get(node.id)!;
    const streamEnd = node.workstream_id ? positions.get(streamNodes.get(node.workstream_id)?.at(-1)?.id ?? node.id)! : position;
    occupied.set(lane, Math.max(occupied.get(lane) ?? 0, lastConsumer.get(node.id) ?? position, streamEnd));
  }
  const needle = query.trim().toLocaleLowerCase();
  const matchIds = needle ? index.activities.filter(node => (node.label + ' ' + (node.actor_label ?? '') + ' '
    + (node.workstream_label ?? '') + ' ' + (node.detail ?? '') + ' ' + node.id).toLocaleLowerCase().includes(needle)).map(node => node.id) : [];
  let top = 12;
  const rows: PositionedRow[] = index.activities.map(node => {
    const lane = memory.nodes.get(node.id) ?? 0;
    const actor = node.actor_id || node.workstream_id;
    if (actor && !memory.actors.has(actor)) memory.actors.set(actor, memory.actors.size % 6);
    const height = GRAPH_ROW_HEIGHT + (node.id === selected ? detailHeight : 0);
    const row = { id: node.id, node, top, y: top + GRAPH_ROW_HEIGHT / 2, height, lane,
      color: actor ? memory.actors.get(actor)! : lane,
      stream: node.workstream_id ?? '' };
    top += height; return row;
  });
  const ports = new Map(rows.map(row => [row.id, row]));
  const tracks: GraphTrack[] = [], unavailable: ExecutionEdge[] = [];
  for (const edge of index.edges) {
    const source = ports.get(edge.source), target = ports.get(edge.target);
    if (!source || !target) { unavailable.push(edge); continue; }
    tracks.push({ id: edge.id, source, target, edge, kind: edge.kind, color: source.color, reference: false });
  }
  const previous = new Map<string, PositionedRow>();
  for (const row of rows) {
    const key = row.stream || '__history__', prior = previous.get(key);
    if (prior && !(index.outgoing.get(prior.id) ?? []).some(edge => edge.target === row.id)) {
      tracks.push({ id: 'order:' + prior.id + ':' + row.id, source: prior, target: row,
        kind: row.stream ? 'workstream' : 'history', color: row.color, reference: false });
    }
    previous.set(key, row);
  }
  const laneRows = Array.from({ length: GRAPH_LANES }, (_, lane) => rows.filter(row => row.lane === lane));
  for (const track of tracks) {
    const start = Math.min(track.source.y, track.target.y), end = Math.max(track.source.y, track.target.y);
    const blockers = laneRows[track.source.lane];
    let lo = 0, hi = blockers.length;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (blockers[mid].y <= start) lo = mid + 1; else hi = mid; }
    // Operations of the same recorded task belong on its continuous rail.
    // Only another task forces an explicit reference instead of a false join.
    while (lo < blockers.length && blockers[lo].y < end && track.source.stream && blockers[lo].stream === track.source.stream) lo++;
    track.reference = lo < blockers.length && blockers[lo].y < end;
    if (track.kind === 'history') track.reference = false;
  }
  return { rows, ports, tracks, unavailable, matchIds, height: top + 14,
    lanes: Math.max(1, ...rows.map(row => row.lane + 1)) };
}

export function trackPath(track: GraphTrack, x: (lane: number) => number, highlighted = false) {
  const sx = x(track.source.lane), tx = x(track.target.lane), sy = track.source.y, ty = track.target.y;
  if (track.kind === 'history' && sx !== tx) return 'M' + sx + ',' + sy + ' C8,' + sy + ' 8,' + sy + ' 8,' + (sy + 16) + ' V' + (ty - 16) + ' Q8,' + ty + ' ' + tx + ',' + ty;
  if (track.reference) {
    if (highlighted) return 'M' + sx + ',' + sy + ' C8,' + sy + ' 8,' + (sy + 14) + ' 8,' + (sy + 22) + ' V' + (ty - 22) + ' C8,' + (ty - 14) + ' 8,' + ty + ' ' + tx + ',' + ty;
    return 'M' + sx + ',' + sy + ' q-7,0 -7,9 v7 M' + (tx - 7) + ',' + (ty - 16) + ' v7 q0,9 7,9';
  }
  if (sx === tx && sy !== ty) return 'M' + sx + ',' + sy + ' V' + ty;
  if (sy === ty) return 'M' + sx + ',' + (sy - 6) + ' C' + (sx + 20) + ',' + (sy - 22) + ' ' + (sx + 20) + ',' + (sy + 22) + ' ' + sx + ',' + (sy + 6);
  const direction = Math.sign(ty - sy), bend = Math.min(26, Math.abs(ty - sy) / 2);
  return 'M' + sx + ',' + sy + ' V' + (ty - bend * 2 * direction) + ' C' + sx + ',' + (ty - bend * direction) + ' ' + tx + ',' + (ty - bend * direction) + ' ' + tx + ',' + ty;
}
