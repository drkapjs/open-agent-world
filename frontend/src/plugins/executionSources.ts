import { useMemo } from 'react';
import { worldApi } from '../api/client';
import { useCardStateSession } from '../state/cardState';
import { useWorldStore } from '../state/worldStore';

export interface ExecutionNode {
  id: string; label: string; kind: 'agent' | 'plan' | 'tool'; status: string;
  parent_id?: string | null; dependency_ids?: string[]; detail?: string; result?: string;
  role?: string; execution_id?: string | null; plan_id?: string | null; sequence?: number;
  started_at?: string | null; finished_at?: string | null; attempt?: number;
  retry_of?: string | null; replan_of?: string | null; artifact_refs?: string[];
  actor_id?: string | null; actor_label?: string; workstream_id?: string | null; workstream_label?: string;
  operation?: string; record_type?: 'activity' | 'context' | 'planned'; realized_by?: string | null;
}
export interface ExecutionEdge {
  id: string; source: string; target: string; kind: 'control' | 'data' | 'retry' | 'replan' | 'fork' | 'merge';
  artifact_ref?: string | null; evidence?: string | null;
}
export interface ExecutionPage { cursor?: string; limit?: number }
export interface ExecutionSnapshot {
  run_id?: string; previous_run_id?: string; history_id?: string; revision?: string | number;
  title: string; phase: string; nodes: ExecutionNode[]; edges?: ExecutionEdge[];
  mode?: 'full' | 'page';
  retention?: 'snapshot' | 'history';
  truncated?: boolean; next_cursor?: string | null; total_nodes?: number | null;
}
export interface ExecutionSourceHost {
  list(): Promise<{ sources: { id: string; name: string; type: string }[] }>;
  read(source: string, page?: ExecutionPage): Promise<ExecutionSnapshot>;
  subscribe(listener: (reason?: 'connections' | 'execution') => void): () => void;
}

/** Session and live connection changes invalidate outstanding projections. */
export function useExecutionSources(cardId: string, disabled = false): ExecutionSourceHost | undefined {
  const session = useCardStateSession(cardId);
  return useMemo(() => disabled ? undefined : {
    list: () => worldApi.executionSources(cardId),
    read: (source, page) => worldApi.executionSnapshot(cardId, source, session ?? null, page),
    subscribe: listener => useWorldStore.subscribe((next, previous) => {
      const links = (state: typeof next) => state.edges.filter(edge => edge.source === cardId)
        .map(edge => `${edge.id}:${edge.target}:${edge.relationship}:${edge.direction}`).sort().join('|');
      if (next.edges !== previous.edges && links(next) !== links(previous)) {
        listener('connections');
        return;
      }
      // Descendants and summoned agents need not be directly connected to the
      // viewer. The read API resolves ancestry and filters the active session.
      const event = next.events !== previous.events ? next.events[0] : undefined;
      if (event && (!session || !event.session_id || event.session_id === session)) {
        const type = event.type.toLowerCase();
        if (type === 'execution_updated' || type.startsWith('run_')) {
          listener('execution');
          return;
        }
        if (type.startsWith('state_') && (!session || !event.payload.state_session_id
          || event.payload.state_scope !== 'session' || event.payload.state_session_id === session)) {
          const sources = new Set(next.edges.filter(edge => edge.source === cardId).map(edge => edge.target));
          // Custom sources and their connected libraries own scoped state.
          const owners = new Set([...sources, ...next.edges.filter(edge => sources.has(edge.source)).map(edge => edge.target)]);
          if (owners.has(String(event.payload.owner_id ?? event.node_id))) listener('execution');
        }
      }
      // Reconnects and indirect connection changes (e.g. a Summoning card
      // losing its library) also invalidate projections. Polling is a fallback.
      if (next.eventStream !== previous.eventStream || next.edges !== previous.edges) listener('execution');
    }),
  }, [cardId, session, disabled]);
}
