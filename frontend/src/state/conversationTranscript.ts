import type { ConversationMessage, ConversationRunSummary } from '../types/world';
import { withoutFinalReply, type RunActivityItem, type RunActivityState } from './runActivity';

type MessageEntry = { kind: 'message'; id: string; timestamp: string; message: ConversationMessage };
type ActivityEntry = { kind: 'activity'; id: string; timestamp: string; run: ConversationRunSummary; item: RunActivityItem };
export type ExecutionGroup = {
  kind: 'execution'; id: string; timestamp: string; run: ConversationRunSummary;
  items: RunActivityItem[]; running: boolean; expanded: boolean; truncated: boolean;
};
export type TranscriptEntry = MessageEntry | ActivityEntry | ExecutionGroup;
export type TranscriptWindow = { hasBefore?: boolean; hasAfter?: boolean };
const isRunning = (run: ConversationRunSummary) => ['created', 'running', 'waiting'].includes(run.status);
function compareTimestamp(a: string, b: string): number {
  const milliseconds = (Date.parse(a) || 0) - (Date.parse(b) || 0);
  if (milliseconds) return milliseconds;
  // Backend records carry microseconds. Date.parse alone can move narration
  // in front of a tool that started earlier within the same millisecond.
  const fraction = (value: string) => (value.match(/\.(\d+)(?:Z|[+-]\d\d:\d\d)$/)?.[1] ?? '').padEnd(6, '0');
  return fraction(a).localeCompare(fraction(b));
}

/** One projection for live and restored history. Groups are presentation only:
 * adjacent tool calls in the same Run, separated by every message/other actor.
 * Completion updates the original call; it never moves activity to the reply. */
export function conversationTranscript(messages: ConversationMessage[], runs: ConversationRunSummary[],
  activities: Map<string, RunActivityState>, window: TranscriptWindow = {}): TranscriptEntry[] {
  const ids = new Set(messages.map(message => message.id));
  const entries: (MessageEntry | ActivityEntry)[] = messages.map(message => ({ kind: 'message', id: message.id,
    timestamp: message.created_at, message }));
  const first = Date.parse(messages[0]?.created_at ?? '') || 0;
  const last = Date.parse(messages.at(-1)?.created_at ?? '') || Infinity;
  for (const run of runs) {
    const replies = messages.filter(message => message.run_id === run.run_id && message.sender_kind === 'agent');
    const activity = activities.get(run.run_id);
    // Legacy providers lack identity; only their final snapshot can be matched
    // by content, and only after the Run ends.
    const source = isRunning(run) ? activity : withoutFinalReply(activity,
      replies.at(-1)?.is_final !== false ? replies.at(-1)?.content : undefined);
    for (const item of source?.items ?? []) {
      if (item.conversation_message_id && ids.has(item.conversation_message_id)) continue;
      const timestamp = item.timestamp || run.started_at || replies[0]?.created_at || '';
      const time = Date.parse(timestamp) || 0;
      // Run summaries cover whole Runs, whereas the message window is paged.
      if ((window.hasBefore && time < first) || (window.hasAfter && time > last)) continue;
      const id = item.type.startsWith('tool_') && item.call_id ? `call:${item.call_id}` : item.id;
      entries.push({ kind: 'activity', id: `${run.run_id}:${id}`, timestamp, run, item });
    }
  }
  entries.sort((a, b) => {
    if (a.kind === 'message' && b.kind === 'message' && a.message.sequence && b.message.sequence) {
      return a.message.sequence - b.message.sequence;
    }
    return compareTimestamp(a.timestamp, b.timestamp);
  });
  const result: TranscriptEntry[] = [];
  const firstGroup = new Set<string>();
  for (const entry of entries) {
    if (entry.kind !== 'activity' || !entry.item.type.startsWith('tool_')) {
      result.push(entry);
      continue;
    }
    const previous = result.at(-1);
    if (previous?.kind === 'execution' && previous.run.run_id === entry.run.run_id) {
      previous.items.push(entry.item);
    } else {
      result.push({ kind: 'execution', id: entry.id, timestamp: entry.timestamp, run: entry.run,
        items: [entry.item], running: isRunning(entry.run), expanded: false,
        truncated: !firstGroup.has(entry.run.run_id) && Boolean(activities.get(entry.run.run_id)?.truncated) });
      firstGroup.add(entry.run.run_id);
    }
  }
  const latestExecution = new Map(result.filter(entry => entry.kind === 'execution').map(entry => [entry.run.run_id, entry.id]));
  for (let index = 0; index < result.length; index += 1) {
    const entry = result[index];
    if (entry.kind !== 'execution' || !entry.running) continue;
    // Retain the latest work while the Agent reports/awaits its next model turn.
    // A new execution segment or Run termination archives it, not narration.
    entry.expanded = entry.items.some(item => item.type === 'tool_started') || latestExecution.get(entry.run.run_id) === entry.id;
  }
  return result;
}
