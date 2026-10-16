import { expect, it } from 'vitest';
import { elapsedMilliseconds, executionElapsed, toolElapsed } from './activityTiming';
import type { RunActivityItem } from './runActivity';

const at = (seconds: number) => new Date(seconds * 1000).toISOString();
const call = (id: string, start: number, end: number): RunActivityItem => ({ id, type: 'tool_completed',
  started_at: at(start), finished_at: at(end) });

it('uses each call interval and the wall-clock span of parallel calls', () => {
  const items = [call('a', 1, 5), call('b', 2, 7)];
  expect(toolElapsed(items[0], false, 0)).toBe(4000);
  expect(executionElapsed(items, false, 0)).toBe(6000);
});

it('ticks a running call but never fabricates a duration for missing or invalid timestamps', () => {
  const pending: RunActivityItem = { id: 'a', type: 'tool_started', started_at: at(1) };
  expect(toolElapsed(pending, true, 4500)).toBe(3500);
  expect(toolElapsed(pending, false, 4500)).toBeUndefined();
  expect(toolElapsed({ id: 'old', type: 'tool_completed', timestamp: at(1) }, false, 4500)).toBeUndefined();
  expect(elapsedMilliseconds(at(2), at(1))).toBeUndefined();
  expect(elapsedMilliseconds('invalid', at(1))).toBeUndefined();
  expect(executionElapsed([call('a', 1, 2), pending], false, 4500)).toBeUndefined();
});
