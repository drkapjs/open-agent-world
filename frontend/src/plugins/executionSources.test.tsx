// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useWorldStore } from '../state/worldStore';
import { useExecutionSources } from './executionSources';
import type { RuntimeEvent } from '../types/world';

vi.mock('../state/cardState', () => ({ useCardStateSession: () => 'session-a' }));
afterEach(cleanup);

it('notifies progress for the active session, source state and library changes without resetting the viewer', () => {
  useWorldStore.setState({ events: [], edges: [
    { id: 'view-source', source: 'view', target: 'source', relationship: 'core.execution-source', direction: 'forward' },
    { id: 'source-library', source: 'source', target: 'library', relationship: 'summon', direction: 'forward' },
  ] as never });
  const { result } = renderHook(() => useExecutionSources('view'));
  const listener = vi.fn();
  const unsubscribe = result.current!.subscribe(listener);
  const emit = (type: string, extras: Partial<RuntimeEvent> = {}) => act(() => useWorldStore.setState({ events: [
    { id: crypto.randomUUID(), type, timestamp: '', session_id: 'session-a', payload: {}, ...extras },
  ] }));
  emit('execution_updated');
  emit('run_succeeded', { agent_id: 'indirect-child' });
  emit('execution_updated', { session_id: 'session-b' });
  emit('agent_message');
  emit('state_updated', { payload: { owner_id: 'library', state_scope: 'session', state_session_id: 'session-a' } });
  emit('state_updated', { payload: { owner_id: 'library', state_scope: 'session', state_session_id: 'session-b' } });
  emit('state_updated', { payload: { owner_id: 'unrelated' } });
  expect(listener.mock.calls).toEqual([['execution'], ['execution'], ['execution']]);
  act(() => useWorldStore.setState(state => ({ edges: state.edges.slice(0, 1) })));
  expect(listener).toHaveBeenLastCalledWith('execution');
  act(() => useWorldStore.setState({ edges: [] }));
  expect(listener).toHaveBeenLastCalledWith('connections');
  unsubscribe();
  emit('execution_updated');
  expect(listener).toHaveBeenCalledTimes(5);
});
