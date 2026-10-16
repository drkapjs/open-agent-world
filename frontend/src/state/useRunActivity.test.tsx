// @vitest-environment jsdom
import { renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import type { ConversationRunSummary, RuntimeEvent } from '../types/world';
import { useRunActivity } from './useRunActivity';

it('retains activity for every visible Run even when the message window exceeds the idle cache size', () => {
  const summaries = Object.fromEntries(Array.from({ length: 70 }, (_, i) => {
    const run: ConversationRunSummary = { run_id: `run-${i}`, agent_id: 'atlas', status: 'succeeded', tool_count: 1,
      tool_trace: [{ id: `tool-${i}`, type: 'tool_completed', name: 'read' }] };
    return [run.run_id, run];
  }));
  const { result, rerender } = renderHook(({ session }) => useRunActivity('room', session, [], [], session === 'a' ? summaries : {}),
    { initialProps: { session: 'a' } });
  expect(result.current.activities.size).toBe(70);
  expect(result.current.runs).toHaveLength(70);
  rerender({ session: 'b' });
  expect(result.current.activities.size).toBe(0);
  expect(result.current.runs).toHaveLength(0);
});

it('retains a terminal event-only tail while REST catches up without resurrecting its running status', () => {
  const run: ConversationRunSummary = { run_id: 'run', agent_id: 'atlas', status: 'running', tool_count: 0, tool_trace: [] };
  const event: RuntimeEvent = { id: 'tool', type: 'tool_completed', timestamp: '2026-10-14T00:00:00Z',
    conversation_id: 'room', session_id: 'session', agent_id: 'atlas', run_id: 'run', payload: { name: 'read' } };
  const { result, rerender } = renderHook(({ active }) => useRunActivity('room', 'session', [event], active, { run }),
    { initialProps: { active: [run] } });
  expect(result.current.runs[0].status).toBe('running');
  rerender({ active: [] });
  expect(result.current.runs[0].status).toBe('');
  expect(result.current.activities.get('run')?.items[0].name).toBe('read');
});
