import { expect, it } from 'vitest';
import { conversationSections } from './conversationSections';
import { conversationTurns } from './conversationTurns';
import type { ConversationRunSummary } from '../types/world';
import type { TranscriptEntry } from './conversationTranscript';

const run: ConversationRunSummary = { run_id: 'run', agent_id: 'atlas', status: 'succeeded',
  final_message_id: 'answer', tool_count: 1, tool_trace: [] };
const message = (id: string, sender: 'user' | 'agent' = 'agent', runId = 'run'): TranscriptEntry => ({
  kind: 'message', id, timestamp: '', message: { id, conversation_id: 'room', session_id: 'session',
    sender_kind: sender, sender_id: sender === 'agent' ? 'atlas' : undefined, sender_name: sender,
    run_id: runId, content: id, created_at: '', mention_agent_ids: [], is_final: true } });
const tool: TranscriptEntry = { kind: 'execution', id: 'tools', timestamp: '', run,
  items: [], running: false, expanded: false, truncated: false };

it('folds the whole process immediately before the recorded final reply, keeping the reply visible', () => {
  const result = conversationSections([message('intro'), tool, message('update'), message('answer')], [run]);
  expect(result.map(section => section.kind)).toEqual(['history', 'message']);
  expect(result[0]).toMatchObject({ entries: [{ id: 'intro' }, { id: 'tools' }, { id: 'update' }] });
  expect(result[1].id).toBe('answer');
});

it('never infers a final reply from sealed messages, a stopped Run or an unavailable page', () => {
  const entries = [message('intro'), tool, message('answer')];
  for (const record of [{ ...run, status: 'running' }, { ...run, status: 'failed' },
    { ...run, final_message_id: undefined }, { ...run, final_message_id: 'off-page' }]) {
    expect(conversationSections(entries, [record])).toEqual(entries);
  }
});

it('keeps interruptions in place and never includes another speaker or Run in a history block', () => {
  const entries = [message('intro'), tool, message('followup', 'user'), message('other', 'agent', 'other'), message('answer')];
  const turns = conversationTurns(entries, [run], []);
  const sections = turns.flatMap(turn => conversationSections(turn.entries, [run], entries));
  expect(sections.map(section => section.kind === 'history' ? section.entries.map(entry => entry.id) : section.id))
    .toEqual([['intro', 'tools'], 'followup', 'other', 'answer']);
});

it('keeps separate successful responses from the same speaker separate', () => {
  const second = { ...run, run_id: 'second', final_message_id: 'answer2' };
  const sections = conversationSections([message('intro'), message('answer'), message('intro2', 'agent', 'second'),
    message('answer2', 'agent', 'second')], [run, second]);
  expect(sections.map(section => section.kind)).toEqual(['history', 'message', 'history', 'message']);
});
