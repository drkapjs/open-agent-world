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
    expect(before.map(item => item.id)).toEqual(['first', 'run:reply', 'run:b', 'followup']);
    const updated = mergeRunActivity(initial, run, [
      event('c', 'tool_started', 6, { name: 'c', call_id: 'c' }),
      event('updated', 'agent_message', 5, { text: 'Checking resources', provider_message_id: 'reply' }),
      event('b-done', 'tool_completed', 5, { name: 'b', call_id: 'b', response: 'done' }),
    ]);
    expect(conversationTranscript(messages, [run], new Map([[run.run_id, updated]])).map(item => item.id))
      .toEqual(['first', 'run:reply', 'run:b', 'followup', 'run:c']);
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
});
