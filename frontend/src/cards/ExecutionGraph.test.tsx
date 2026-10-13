// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExecutionGraphView, readExecutionHistory, readProjectHistory } from './ExecutionGraph';
import { executionIndex, layoutGraph, laneMemory, trackPath } from './executionHistory';
import type { ExecutionEdge, ExecutionNode, ExecutionSnapshot, ExecutionSourceHost } from '../plugins/executionSources';

beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const node = (id: string, extra: Partial<ExecutionNode> = {}): ExecutionNode => ({ id, label: id, kind: 'agent', status: 'success', ...extra });
const edge = (source: string, target: string, kind: ExecutionEdge['kind'] = 'control'): ExecutionEdge => ({ id: source + ':' + target + ':' + kind, source, target, kind });
const history = (nodes: ExecutionNode[] = [node('A'), node('B'), node('C')], edges: ExecutionEdge[] = []): ExecutionSnapshot => ({
  history_id: 'session-history', revision: 1, run_id: 'run', phase: 'running', title: 'Study copper', nodes, edges,
});
function source(value = history()): ExecutionSourceHost {
  return { list: vi.fn().mockResolvedValue({ sources: [{ id: 'a', name: 'Researcher', type: 'agent' }] }),
    read: vi.fn().mockResolvedValue(value), subscribe: vi.fn(() => () => {}) };
}
it('connects independent history with a noncausal guide, without fabricating dependencies', async () => {
  const index = executionIndex(history()), graph = layoutGraph(index, laneMemory());
  expect(graph.tracks.map(track => track.kind)).toEqual(['history', 'history']);
  expect(graph.height).toBeLessThan(200);
  const view = render(<ExecutionGraphView api={source()} />);
  await screen.findByRole('button', { name: 'A: success' });
  expect(view.container.querySelectorAll('[data-graph-edge]')).toHaveLength(0);
  expect(view.container.querySelectorAll('[data-kind="history"]')).toHaveLength(3); // two rails + key
  const nodes = [node('A'), node('B', { parent_id: 'A', dependency_ids: ['A'] })];
  expect(executionIndex({ ...history(nodes), edges: undefined }).edges.map(edge => edge.kind)).toEqual(['control']);
  expect(executionIndex(history(nodes)).edges).toEqual([]);
});
it('uses global chronology rather than placing all children under an execution', () => {
  const nodes = [
    node('context', { record_type: 'context', sequence: 0 }),
    node('task-a', { sequence: 1 }), node('task-b', { sequence: 2 }),
    node('a-edit', { parent_id: 'task-a', sequence: 4 }),
    node('b-check', { parent_id: 'task-b', sequence: 3 }),
    node('future', { record_type: 'planned', parent_id: 'context' }),
  ];
  expect(executionIndex(history(nodes)).activities.map(node => node.id)).toEqual(['task-a', 'task-b', 'b-check', 'a-edit']);
});
it('draws real forks and joins and keeps distant sources in the same graph', () => {
  const nodes = ['A','B','C','D'].map(id => node(id));
  const edges = [edge('A','B'),edge('A','C'),edge('B','D'),edge('C','D')];
  const graph = layoutGraph(executionIndex(history(nodes, edges)), laneMemory());
  expect(graph.rows[1].lane).not.toBe(graph.rows[2].lane);
  expect(graph.tracks.filter(track => track.edge)).toHaveLength(4);
  expect(graph.tracks.some(track => trackPath(track, lane => 24 + lane * 24).includes('C'))).toBe(true);
  const many = Array.from({ length: 20 }, (_, i) => node('E' + (i + 1)));
  const sparse = layoutGraph(executionIndex(history(many, [edge('E1','E20'),edge('E9','E20'),edge('E17','E20')])), laneMemory());
  expect(sparse.rows).toHaveLength(20);
  expect(sparse.tracks.filter(track => track.edge)).toHaveLength(3);
});
it('keeps ownership colors and lanes through completion, details and late dependencies', () => {
  const nodes = [
    node('a1', { actor_id: 'builder', workstream_id: 'a', status: 'running' }),
    node('b1', { actor_id: 'tester', workstream_id: 'b', status: 'running' }),
    node('a2', { actor_id: 'builder', workstream_id: 'a' }),
    node('b2', { actor_id: 'tester', workstream_id: 'b' }),
  ];
  const memory = laneMemory();
  const first = layoutGraph(executionIndex(history(nodes)), memory);
  const signature = first.rows.map(({ id, lane, color }) => ({ id, lane, color }));
  expect(first.rows[0].lane).toBe(first.rows[2].lane);
  expect(first.rows[0].lane).not.toBe(first.rows[1].lane);
  const update = layoutGraph(executionIndex(history([...nodes.map(item => ({ ...item, status: 'success' })), node('c')], [edge('b2','c')])), memory, 'a1', 200);
  expect(update.rows.slice(0, 4).map(({ id, lane, color }) => ({ id, lane, color }))).toEqual(signature);
});
it('keeps a task continuous through its tool operations before handing off to another task', () => {
  const nodes = [node('a', { workstream_id: 'task-a' }), node('tool', { workstream_id: 'task-a', kind: 'tool' }), node('b', { workstream_id: 'task-b' })];
  const graph = layoutGraph(executionIndex(history(nodes, [edge('a','b')])), laneMemory());
  expect(graph.tracks.find(track => track.edge)?.reference).toBe(false);
});
it('never folds 1200 operations or draws a shared rail through an unrelated node', () => {
  const nodes = Array.from({ length: 1200 }, (_, i) => node('n' + i, { sequence: i }));
  const edges = nodes.slice(1, 30).flatMap(node => [edge('n0', node.id), edge(node.id, 'n1199')]);
  const graph = layoutGraph(executionIndex(history(nodes, edges)), laneMemory());
  expect(graph.rows).toHaveLength(1200); expect(graph.height).toBeGreaterThan(1200 * 54);
  expect(graph.lanes).toBeLessThanOrEqual(4);
  expect(graph.tracks.filter(track => track.edge)).toHaveLength(edges.length);
  expect(graph.tracks.some(track => track.reference)).toBe(true);
  for (const track of graph.tracks.filter(track => track.edge && !track.reference)) {
    expect(graph.rows.some(row => row.id !== track.source.id && row.id !== track.target.id && row.lane === track.source.lane
      && row.y > track.source.y && row.y < track.target.y)).toBe(false);
  }
});
it('opens details from the whole row and closes with the same click or Escape', async () => {
  const view = render(<ExecutionGraphView api={source(history([node('Edit file', { detail: 'Changed the parser.' })]))} />);
  const button = await screen.findByRole('button', { name: 'Edit file: success' });
  fireEvent.click(button.querySelector('.execution-vertex-dot')!);
  expect(screen.getByText('Changed the parser.')).toBeTruthy();
  fireEvent.click(button.querySelector('strong')!);
  expect(screen.queryByText('Changed the parser.')).toBeNull();
  fireEvent.click(button);
  fireEvent.keyDown(screen.getByLabelText('Execution history'), { key: 'Escape' });
  expect(screen.queryByText('Changed the parser.')).toBeNull();
  expect(view.container.querySelector('.execution-expand, .execution-close-selection, .execution-selection-actions')).toBeNull();
});
it('retains planned definitions as planning context and reveals actual attempts directly', async () => {
  const nodes = [
    node('plan', { role: 'planning', detail: 'Build and verify.' }),
    node('definition', { label: 'Build', record_type: 'planned', parent_id: 'plan', realized_by: 'attempt' }),
    node('attempt', { label: 'Build implementation', status: 'failed' }),
  ];
  render(<ExecutionGraphView api={source(history(nodes))} />);
  fireEvent.click(await screen.findByRole('button', { name: 'plan: success' }));
  expect(screen.getByText('Planned work')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Build' }));
  expect(screen.getByRole('button', { name: 'Build implementation: failed' }).getAttribute('aria-expanded')).toBe('true');
});
it('shows exact dependency endpoints, including unavailable ones, without a separate focus view', async () => {
  const view = render(<ExecutionGraphView api={source(history([node('A'), node('B')], [edge('A','B','data'), edge('missing','B')]))} />);
  fireEvent.click(await screen.findByRole('button', { name: 'B: success' }));
  expect(screen.getByRole('button', { name: 'Requires missing' }).hasAttribute('disabled')).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Uses result A' }));
  expect(screen.getByRole('button', { name: 'A: success' }).getAttribute('aria-expanded')).toBe('true');
  expect(view.container.querySelectorAll('.execution-tracks')).toHaveLength(1);
});
it('assembles all pages atomically and rejects mixed revisions and repeated cursors', async () => {
  const api = source();
  api.read = vi.fn().mockResolvedValueOnce({ ...history([node('A')]), next_cursor: 'page2' }).mockResolvedValueOnce(history([node('B')], [edge('A','B')]));
  const assembled = await readExecutionHistory(api, 'a');
  expect(assembled.nodes).toHaveLength(2); expect(assembled.edges).toHaveLength(1);
  api.read = vi.fn().mockResolvedValueOnce({ ...history(), next_cursor: 'page2' }).mockResolvedValueOnce({ ...history(), revision: 2 });
  await expect(readExecutionHistory(api, 'a')).rejects.toThrow('changed while loading');
  api.read = vi.fn().mockResolvedValue({ ...history(), next_cursor: 'repeated' });
  await expect(readExecutionHistory(api, 'a')).rejects.toThrow('repeated cursor');
});
it('combines connected agents without colliding identities or manufacturing cross-agent edges', async () => {
  const api = source(history([node('a'), node('b')], [edge('a','b')]));
  const result = await readProjectHistory(api, [{ id: 'builder', name: 'Builder' }, { id: 'tester', name: 'Tester' }]);
  expect(new Set(result.nodes.map(node => node.id)).size).toBe(4);
  expect(result.nodes.map(node => node.actor_label)).toEqual(['Builder', 'Builder', 'Tester', 'Tester']);
  expect(result.edges).toHaveLength(2);
  expect(result.edges!.every(edge => JSON.parse(edge.source)[0] === JSON.parse(edge.target)[0])).toBe(true);
});
it('preserves opened operation during coalesced updates and new runs in one history', async () => {
  let changed!: Parameters<ExecutionSourceHost['subscribe']>[0];
  const pending: ((value: ExecutionSnapshot) => void)[] = [];
  const value = history([node('Edit', { status: 'running', workstream_id: 'task', actor_id: 'builder' })]);
  const api = source(value);
  api.subscribe = listener => { changed = listener; return () => {}; };
  api.read = vi.fn().mockResolvedValueOnce(value).mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  render(<ExecutionGraphView api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Edit: running' }));
  act(() => changed('execution'));
  await waitFor(() => expect(pending).toHaveLength(1));
  act(() => { changed('execution'); changed('execution'); });
  expect(api.read).toHaveBeenCalledTimes(2);
  const done = { ...value, run_id: 'next-run', revision: 2, nodes: [node('Edit', { status: 'failed', result: 'Validation failed.' }), node('Retry', { retry_of: 'Edit' })] };
  await act(async () => pending[0](done));
  expect((await screen.findByRole('button', { name: 'Edit: failed' })).getAttribute('aria-expanded')).toBe('true');
  expect(screen.getByText('Validation failed.')).toBeTruthy();
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1](done));
});
it('clears revoked connections and ignores an in-flight response from the previous session', async () => {
  let changed!: Parameters<ExecutionSourceHost['subscribe']>[0];
  let finish!: (value: ExecutionSnapshot) => void;
  const api = source();
  api.subscribe = listener => { changed = listener; return () => {}; };
  api.read = vi.fn().mockResolvedValueOnce(history()).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<ExecutionGraphView api={api} />);
  await screen.findByText('Study copper');
  act(() => changed('execution'));
  await waitFor(() => expect(api.read).toHaveBeenCalledTimes(2));
  api.list = vi.fn().mockResolvedValue({ sources: [] });
  act(() => changed('connections'));
  await act(async () => finish(history()));
  await waitFor(() => expect(screen.queryByText('Study copper')).toBeNull());
  view.rerender(<ExecutionGraphView api={source({ title: '', phase: 'idle', nodes: [] })} />);
  await screen.findByText('No execution yet');
});
