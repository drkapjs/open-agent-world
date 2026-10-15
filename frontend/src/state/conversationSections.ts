import type { ConversationRunSummary } from '../types/world';
import type { TranscriptEntry } from './conversationTranscript';

export type ConversationHistorySection = {
  kind: 'history'; id: string; run: ConversationRunSummary; entries: TranscriptEntry[];
};
export type ConversationSection = TranscriptEntry | ConversationHistorySection;

/** Collapse only known successful Run history before its recorded final reply.
 * Speaker/user interruptions and other Runs remain chronological boundaries.
 * Missing final replies (including paged-out replies) never hide the process. */
export function conversationSections(entries: TranscriptEntry[], runs: ConversationRunSummary[],
  transcript: TranscriptEntry[] = entries): ConversationSection[] {
  const positions = new Map(transcript.map((entry, index) => [entry.id, index]));
  const complete = new Map(runs.filter(run => run.status === 'succeeded' && run.final_message_id
    && transcript.some(entry => entry.kind === 'message' && entry.id === run.final_message_id
      && entry.message.run_id === run.run_id && entry.message.sender_kind === 'agent'))
    .map(run => [run.run_id, run]));
  const sections: ConversationSection[] = [];
  for (const entry of entries) {
    const runId = entry.kind === 'message' ? entry.message.run_id : entry.run.run_id;
    const run = runId ? complete.get(runId) : undefined;
    if (!run || (entry.kind === 'message' && entry.message.sender_kind !== 'agent')
      || positions.get(entry.id)! >= positions.get(run.final_message_id!)!) {
      sections.push(entry);
      continue;
    }
    const previous = sections.at(-1);
    if (previous?.kind === 'history' && previous.run.run_id === runId) previous.entries.push(entry);
    else sections.push({ kind: 'history', id: `history:${runId}:${entry.id}`, run, entries: [entry] });
  }
  return sections;
}
