import { expect, it } from 'vitest';
import { conversationTurns } from './conversationTurns';
import type { TranscriptEntry } from './conversationTranscript';
import type { ConversationRunSummary } from '../types/world';

const run: ConversationRunSummary = { run_id: 'run', agent_id: 'atlas', status: 'running', tool_count: 0, tool_trace: [] };
const text = (id: string, agent = 'atlas'): TranscriptEntry => ({ kind: 'message', id, timestamp: '', message: {
  id, conversation_id: 'room', session_id: 'session', sender_kind: 'agent', sender_id: agent, sender_name: 'Same display name',
  content: id, mention_agent_ids: [], run_id: 'run', created_at: '',
} });
const execution: TranscriptEntry = { kind: 'execution', id: 'tools', timestamp: '', run, items: [], running: true, expanded: true, truncated: false };

it('gives consecutive text, tools and Working one speaker identity', () => {
  const turns = conversationTurns([text('intro'), execution, text('update')], [run], [run]);
  expect(turns).toHaveLength(1);
  expect(turns[0]).toMatchObject({ kind: 'agent', agentId: 'atlas', entries: [{ id: 'intro' }, { id: 'tools' }, { id: 'update' }], activeRuns: [run] });
});

it('splits by actual speaker even when names match and resumes the original speaker after an interruption', () => {
  const turns = conversationTurns([text('intro'), text('other', 'river'), text('reply')], [run], [run]);
  expect(turns.map(turn => turn.kind === 'agent' && turn.agentId)).toEqual(['atlas', 'river', 'atlas']);
  expect(turns[2]).toMatchObject({ activeRuns: [run] });
});

it('keeps the speaker container stable when an initial live message gains its durable identity', () => {
  const live: TranscriptEntry = { kind: 'activity', id: 'event', timestamp: '', run, item: { id: 'event', type: 'agent_message', text: 'Intro' } };
  const before = conversationTurns([live, execution], [run], [run]);
  const after = conversationTurns([text('durable'), execution], [run], [run]);
  expect(after[0].id).toBe(before[0].id);
});

it('does not invent shared identity for old messages without an Agent or Run reference', () => {
  const unknown = (id: string): TranscriptEntry => {
    const entry = text(id);
    if (entry.kind === 'message') { delete entry.message.sender_id; delete entry.message.run_id; }
    return entry;
  };
  expect(conversationTurns([unknown('one'), unknown('two')], [], [])).toHaveLength(2);
});
