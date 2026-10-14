import type { ConversationRunSummary } from '../types/world';
import type { TranscriptEntry } from './conversationTranscript';

export type ConversationTurn =
  | { kind: 'message'; id: string; entries: TranscriptEntry[] }
  | { kind: 'agent'; id: string; agentId: string; name?: string; entries: TranscriptEntry[]; activeRuns: ConversationRunSummary[] };

/** Identity belongs to a continuous speaker turn, not to every text/tool row.
 * Only recorded sender/Run identity can join turns; names are display labels. */
export function conversationTurns(entries: TranscriptEntry[], runs: ConversationRunSummary[], active: ConversationRunSummary[]): ConversationTurn[] {
  const runAgents = new Map(runs.map(run => [run.run_id, run.agent_id]));
  const turns: ConversationTurn[] = [];
  for (const entry of entries) {
    if (entry.kind === 'message' && entry.message.sender_kind !== 'agent') {
      turns.push({ kind: 'message', id: entry.id, entries: [entry] });
      continue;
    }
    const agentId = entry.kind === 'message'
      ? entry.message.sender_id ?? runAgents.get(entry.message.run_id ?? '') ?? `unknown:${entry.id}`
      : entry.run.agent_id;
    const previous = turns.at(-1);
    if (previous?.kind === 'agent' && previous.agentId === agentId) previous.entries.push(entry);
    else turns.push({ kind: 'agent', id: `speaker:${agentId}:${previous?.entries.at(-1)?.id ?? previous?.id ?? 'start'}`, agentId,
      name: entry.kind === 'message' ? entry.message.sender_name : undefined, entries: [entry], activeRuns: [] });
  }
  for (const run of active) {
    const turn = [...turns].reverse().find(turn => turn.kind === 'agent' && turn.agentId === run.agent_id
      && turn.entries.some(entry => (entry.kind === 'message' ? entry.message.run_id : entry.run.run_id) === run.run_id));
    if (turn?.kind === 'agent') turn.activeRuns.push(run);
    else {
      const previous = turns.at(-1);
      if (previous?.kind === 'agent' && previous.agentId === run.agent_id) previous.activeRuns.push(run);
      else turns.push({ kind: 'agent', id: `speaker:${run.agent_id}:${previous?.entries.at(-1)?.id ?? previous?.id ?? 'start'}`,
        agentId: run.agent_id, entries: [], activeRuns: [run] });
    }
  }
  return turns;
}
