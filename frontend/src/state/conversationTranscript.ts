import type { ConversationMessage, ConversationRunSummary } from '../types/world';
import type { RunActivityItem, RunActivityState } from './runActivity';

type TranscriptEntry =
  | { kind: 'message'; id: string; timestamp: string; message: ConversationMessage }
  | { kind: 'activity'; id: string; timestamp: string; run: ConversationRunSummary; item: RunActivityItem };

/** Merge individual messages and activity, never whole Runs, into arrival order. */
export function conversationTranscript(messages: ConversationMessage[], active: ConversationRunSummary[],
  activities: Map<string, RunActivityState>): TranscriptEntry[] {
  const ids = new Set(messages.map(message => message.id));
  const entries: TranscriptEntry[] = messages.map(message => ({ kind: 'message', id: message.id,
    timestamp: message.created_at, message }));
  for (const run of active) {
    for (const item of activities.get(run.run_id)?.items ?? []) {
      if (item.conversation_message_id && ids.has(item.conversation_message_id)) continue;
      entries.push({ kind: 'activity', id: `${run.run_id}:${item.id}`,
        timestamp: item.timestamp ?? run.started_at ?? '', run, item });
    }
  }
  return entries.sort((a, b) => {
    if (a.kind === 'message' && b.kind === 'message' && a.message.sequence && b.message.sequence) {
      return a.message.sequence - b.message.sequence;
    }
    return (Date.parse(a.timestamp) || 0) - (Date.parse(b.timestamp) || 0);
  });
}
