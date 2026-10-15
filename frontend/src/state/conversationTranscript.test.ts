import { describe, expect, it } from 'vitest';
import { conversationTranscript } from './conversationTranscript';
import { mergeRunActivity } from './runActivity';
import type { ConversationMessage, ConversationRunSummary, RuntimeEvent } from '../types/world';

const time = (second: number) => `2026-10-13T00:00:0${second}Z`;
const message = (id: string, second: number, sequence: number): ConversationMessage => ({
  id, conversation_id: 'room', session_id: 'session', sender_kind: 'user', sender_name: 'You',
  content: id, mention_agent_ids: [], created_at: time(second), sequence,
});
const run: ConversationRunSummary = { run_id: 'run', agent_id: 'agent', status: 'running',
  started_at: time(1), tool_count: 1, tool_trace: [] };
const event = (id: string, type: string, second: number, payload: Record<string, unknown>): RuntimeEvent => ({
  id, type, timestamp: time(second), run_id: run.run_id, agent_id: run.agent_id, payload,
});

describe('conversation arrival order', () => {
  it('interleaves followups with individual activity and keeps a streaming bubble at its first position', () => {
    const events = [event('reply', 'agent_message', 2, { text: 'Checking', provider_message_id: 'reply' }),
      event('b', 'tool_started', 3, { name: 'b', call_id: 'b' })];
    const initial = mergeRunActivity(undefined, run, [...events].reverse());
    const messages = [message('first', 0, 1), message('followup', 4, 2)];
    const before = conversationTranscript(messages, [run], new Map([[run.run_id, initial]]));
    expect(before.map(item => item.id)).toEqual(['first', 'run:reply', 'run:call:b', 'followup']);
    const updated = mergeRunActivity(initial, run, [
      event('c', 'tool_started', 6, { name: 'c', call_id: 'c' }),
      event('updated', 'agent_message', 5, { text: 'Checking resources', provider_message_id: 'reply' }),
      event('b-done', 'tool_completed', 5, { name: 'b', call_id: 'b', response: 'done' }),
    ]);
    expect(conversationTranscript(messages, [run], new Map([[run.run_id, updated]])).map(item => item.id))
      .toEqual(['first', 'run:reply', 'run:call:b', 'followup', 'run:call:c']);
  });

  it('reconciles REST and websocket by message identity and retains the order on reload', () => {
    const messages = [message('first', 0, 1), { ...message('reply', 2, 2), sender_kind: 'agent' as const },
      message('followup', 4, 3)];
    const activity = mergeRunActivity(undefined, run, [event('snapshot', 'agent_message', 5,
      { text: 'Checking', provider_message_id: 'provider-reply', conversation_message_id: 'reply' })]);
    expect(conversationTranscript(messages, [run], new Map([[run.run_id, activity]])).map(item => item.id))
      .toEqual(['first', 'reply', 'followup']);
    expect(conversationTranscript(messages, [], new Map()).map(item => item.id)).toEqual(['first', 'reply', 'followup']);
  });

  it('groups only adjacent same-Run tools and retains segments after completion and reload', () => {
    const trace = [
      event('a', 'tool_started', 1, { call_id: 'a', name: 'read' }),
      event('b', 'tool_started', 2, { call_id: 'b', name: 'read' }),
      event('a-done', 'tool_completed', 3, { call_id: 'a', name: 'read' }),
      event('b-done', 'tool_completed', 3, { call_id: 'b', name: 'read' }),
      event('plan', 'agent_progress', 4, { kind: 'reasoning_summary', text: 'Compare the files' }),
      event('c', 'tool_started', 5, { call_id: 'c', name: 'compare' }),
      event('c-done', 'tool_completed', 6, { call_id: 'c', name: 'compare' }),
    ];
    const active = mergeRunActivity(undefined, run, [...trace].reverse());
    const messages = [message('user', 0, 1)];
    const live = conversationTranscript(messages, [run], new Map([[run.run_id, active]]));
    expect(live.map(entry => entry.kind)).toEqual(['message', 'execution', 'activity', 'execution']);
    expect(live[1]).toMatchObject({ id: 'run:call:a', expanded: false, items: [{ id: 'a' }, { id: 'b' }] });
    expect(live[3]).toMatchObject({ id: 'run:call:c', expanded: true });
    const terminal = { ...run, status: 'succeeded', tool_trace: trace.map(item => ({ ...item.payload,
      id: item.id, timestamp: item.timestamp, type: item.type })) };
    const restored = mergeRunActivity(undefined, terminal, []);
    const final = { ...message('answer', 7, 2), sender_kind: 'agent' as const, run_id: run.run_id };
    const ended = conversationTranscript([...messages, final], [terminal], new Map([[run.run_id, restored]]));
    expect(ended.map(entry => entry.id)).toEqual([...live.map(entry => entry.id), 'answer']);
    expect(ended.filter(entry => entry.kind === 'execution').every(entry => !entry.expanded)).toBe(true);
  });

  it('splits for user followups and other Runs without hiding unfinished calls', () => {
    const other = { ...run, run_id: 'other', agent_id: 'other-agent' };
    const a = mergeRunActivity(undefined, run, [event('c', 'tool_started', 5, { name: 'c' }),
      event('a', 'tool_started', 1, { name: 'a' })]);
    const b = mergeRunActivity(undefined, other, [{ ...event('b', 'tool_started', 3, { name: 'b' }), run_id: 'other' }]);
    const result = conversationTranscript([message('followup', 2, 1)], [run, other], new Map([['run', a], ['other', b]]));
    expect(result.map(entry => entry.id)).toEqual(['run:a', 'followup', 'other:b', 'run:c']);
    expect(result.filter(entry => entry.kind === 'execution').every(entry => entry.expanded)).toBe(true);
  });

  it('restricts whole-Run checkpoints to the visible chronological page', () => {
    const activity = mergeRunActivity(undefined, run, [event('old', 'tool_started', 1, { name: 'old' }),
      event('visible', 'tool_started', 3, { name: 'visible' }), event('new', 'tool_started', 6, { name: 'new' })]);
    const entries = conversationTranscript([message('first', 2, 1), message('last', 4, 2)], [run],
      new Map([['run', activity]]), { hasBefore: true, hasAfter: true });
    expect(entries.map(entry => entry.id)).toEqual(['first', 'run:visible', 'last']);
  });

  it('keeps the latest work visible during the next narration until new execution or Run completion', () => {
    const activity = mergeRunActivity(undefined, run, [event('done', 'tool_completed', 2, { name: 'read', call_id: 'read' }),
      event('start', 'tool_started', 1, { name: 'read', call_id: 'read' })]);
    const entries = conversationTranscript([{ ...message('narration', 3, 1), sender_kind: 'agent' }], [run], new Map([['run', activity]]));
    expect(entries[0]).toMatchObject({ kind: 'execution', expanded: true });
    const stopped = conversationTranscript([], [{ ...run, status: 'succeeded' }], new Map([['run', activity]]));
    expect(stopped[0]).toMatchObject({ kind: 'execution', expanded: false });
  });

  it('retains microsecond order and tool identity when the checkpoint repairs a missing start', () => {
    const reply = { ...message('reply', 1, 1), created_at: '2026-10-13T00:00:01.000600Z' };
    const start = { ...event('start', 'tool_started', 1, { call_id: 'c', name: 'read' }), timestamp: '2026-10-13T00:00:01.000100+00:00' };
    const done = { ...event('done', 'tool_completed', 1, { call_id: 'c', name: 'read' }), timestamp: '2026-10-13T00:00:01.000900Z' };
    const early = mergeRunActivity(undefined, run, [done]);
    const before = conversationTranscript([reply], [run], new Map([['run', early]]));
    const repaired = mergeRunActivity(early, run, [start]);
    const after = conversationTranscript([reply], [run], new Map([['run', repaired]]));
    expect(before.map(entry => entry.id)).toEqual(['reply', 'run:call:c']);
    expect(after.map(entry => entry.id)).toEqual(['run:call:c', 'reply']);
  });
});
