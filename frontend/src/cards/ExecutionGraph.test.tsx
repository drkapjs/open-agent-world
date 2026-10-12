// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ExecutionGraphView } from './ExecutionGraph';
import { executionGraph } from './executionGraphLayout';
import type { ExecutionSnapshot, ExecutionSourceHost } from '../plugins/executionSources';

beforeEach(() => vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const snapshot: ExecutionSnapshot = { phase: 'running', title: 'Study copper', nodes: [
  { id: 'executor', kind: 'agent', label: 'Executor', status: 'running' },
  { id: 'child', kind: 'agent', label: 'Geometry worker', status: 'running', parent_id: 'executor' },
  { id: 'build', kind: 'plan', label: 'Build crystal', status: 'success', result: '32 atoms verified' },
  { id: 'check', kind: 'plan', label: 'Check crystal', status: 'running', dependency_ids: ['build'] },
] };
function source(value = snapshot): ExecutionSourceHost {
  return { list: vi.fn().mockResolvedValue({sources: [{id: 'a', name: 'Researcher', type: 'agent'}]}),
    read: vi.fn().mockResolvedValue(value), subscribe: vi.fn(() => () => {}) };
}
it('distinguishes calls from dependencies and clears the previous session', async () => {
  const api = source();
  const view = render(<ExecutionGraphView api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Geometry worker: running' }));
  expect(view.container.querySelector('[data-graph-edge="executor:child"]')).toBeTruthy();
  expect(screen.getByText('Called by: Executor')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Plan' }));
  fireEvent.click(screen.getByRole('button', { name: 'Build crystal: success' }));
  expect(screen.getByText('32 atoms verified')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Check crystal: running' }));
  expect(screen.getByText('Depends on: Build crystal')).toBeTruthy();
  view.rerender(<ExecutionGraphView api={source({phase: 'idle', title: '', nodes: []})} />);
  await screen.findByText('No execution yet');
  expect(screen.queryByText('Study copper')).toBeNull();
});
it('switches connected sources and discards an in-flight response after revocation', async () => {
  let changed = () => {};
  let finish!: (value: ExecutionSnapshot) => void;
  const api = source();
  api.list = vi.fn().mockResolvedValue({sources: [{id: 'a', name: 'Researcher'}, {id: 'b', name: 'Barracks'}]});
  api.read = vi.fn().mockResolvedValueOnce(snapshot).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  api.subscribe = listener => { changed = listener; return () => {}; };
  render(<ExecutionGraphView api={api} />);
  await screen.findByText('Study copper');
  fireEvent.change(screen.getByLabelText('Execution source'), {target: {value: 'b'}});
  await waitFor(() => expect(api.read).toHaveBeenCalledWith('b'));
  api.list = vi.fn().mockResolvedValue({sources: []});
  changed();
  finish(snapshot);
  await waitFor(() => expect(screen.queryByText('Study copper')).toBeNull());
  await waitFor(() => expect(screen.queryByLabelText('Execution source')).toBeNull());
});
it('keeps positions stable across status ticks and tolerates partial or cyclic sources', () => {
  const value: ExecutionSnapshot = { phase: 'running', title: '', nodes: [
    { id: 'root', kind: 'agent', label: 'Root', status: 'running' },
    { id: 'a', kind: 'agent', label: 'A', status: 'running', parent_id: 'root' },
    { id: 'b', kind: 'agent', label: 'B', status: 'pending', parent_id: 'root', dependency_ids: ['a'] },
    { id: 'orphan', kind: 'agent', label: 'Orphan', status: 'running', parent_id: 'missing' },
  ] };
  const first = executionGraph(value, 'agents');
  value.nodes[1].status = 'success';
  expect(executionGraph(value, 'agents').nodes.map(({id, x, y}) => ({id, x, y})))
    .toEqual(first.nodes.map(({id, x, y}) => ({id, x, y})));
  expect(first.edges).toHaveLength(3);
  expect(first.nodes.find(node => node.id === 'b')!.y).toBeGreaterThan(first.nodes.find(node => node.id === 'a')!.y);
  value.nodes[0].parent_id = 'b';
  expect(executionGraph(value, 'agents').nodes).toHaveLength(4);
});

it('refreshes live without blanking selection and coalesces notifications during a slow read', async () => {
  let changed!: Parameters<ExecutionSourceHost['subscribe']>[0];
  const pending: ((value: ExecutionSnapshot) => void)[] = [];
  const api = source();
  api.subscribe = listener => { changed = listener; return () => {}; };
  api.read = vi.fn().mockResolvedValueOnce(snapshot).mockImplementation(() => new Promise(resolve => pending.push(resolve)));
  const view = render(<ExecutionGraphView api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Geometry worker: running' }));
  const canvas = view.container.querySelector('.execution-graph-canvas') as HTMLElement;
  fireEvent.click(screen.getByRole('button', { name: 'Zoom in' }));
  const camera = canvas.style.transform;
  act(() => changed('execution'));
  await waitFor(() => expect(pending).toHaveLength(1));
  act(() => { changed('execution'); changed('execution'); changed('execution'); });
  expect(api.read).toHaveBeenCalledTimes(2);
  expect(screen.getByRole('region', { name: 'Execution details' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Geometry worker: running' }).getAttribute('aria-pressed')).toBe('true');
  const done = { ...snapshot, nodes: snapshot.nodes.map(node => ({ ...node, status: 'success', result: 'Fresh result' })) };
  await act(async () => pending[0](done));
  await screen.findByRole('button', { name: 'Geometry worker: success' });
  expect(screen.getByText('Fresh result')).toBeTruthy();
  expect(canvas.style.transform).toBe(camera);
  await waitFor(() => expect(pending).toHaveLength(2));
  await act(async () => pending[1](done));
  expect(api.read).toHaveBeenCalledTimes(3);
});

it('rejects a slow response from the previous session after the new session has loaded', async () => {
  let finish!: (value: ExecutionSnapshot) => void;
  const old = source();
  old.read = vi.fn(() => new Promise<ExecutionSnapshot>(resolve => { finish = resolve; }));
  const view = render(<ExecutionGraphView api={old} />);
  await waitFor(() => expect(old.read).toHaveBeenCalled());
  view.rerender(<ExecutionGraphView api={source({ phase: 'idle', title: '', nodes: [] })} />);
  await screen.findByText('No execution yet');
  await act(async () => finish(snapshot));
  expect(screen.queryByRole('button', { name: 'Geometry worker: running' })).toBeNull();
  expect(screen.queryByText('Study copper')).toBeNull();
});

it('retains inspection while a new run starts and labels the graph as previous execution', async () => {
  let changed!: Parameters<ExecutionSourceHost['subscribe']>[0];
  const api = source({ ...snapshot, run_id: 'old' });
  api.subscribe = listener => { changed = listener; return () => {}; };
  render(<ExecutionGraphView api={api} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Geometry worker: running' }));
  api.read = vi.fn().mockResolvedValue({ ...snapshot, run_id: 'new', previous_run_id: 'old', phase: 'running' });
  act(() => changed('execution'));
  await screen.findByText('Waiting for this run’s graph…');
  expect(screen.getByText('Previous task goal')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Geometry worker: running' }).getAttribute('aria-pressed')).toBe('true');
  api.read = vi.fn().mockResolvedValue({ ...snapshot, run_id: 'new' });
  act(() => changed('execution'));
  await waitFor(() => expect(screen.queryByText('Waiting for this run’s graph…')).toBeNull());
  expect(screen.queryByRole('region', { name: 'Execution details' })).toBeNull();
});

it('retains parent and dependency semantics when both connect the same nodes', () => {
  const graph = executionGraph({ title: '', phase: 'running', nodes: [
    { id: 'a', label: 'A', kind: 'agent', status: 'running' },
    { id: 'b', label: 'B', kind: 'agent', status: 'pending', parent_id: 'a', dependency_ids: ['a'] },
  ] }, 'agents');
  expect(graph.edges.map(edge => edge.dependency)).toEqual([false, true]);
  expect(graph.nodes.find(node => node.id === 'b')!.y).toBeGreaterThan(graph.nodes.find(node => node.id === 'a')!.y);
});
