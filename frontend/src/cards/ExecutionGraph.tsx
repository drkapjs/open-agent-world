import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { ArrowDown, ArrowDownRight, ArrowUpRight, Check, ChevronDown, Circle, Clock3, FileCode2, GitBranch, GitMerge, LoaderCircle, Play, Search, Wrench, X } from 'lucide-react';
import { t, useLocale } from '../i18n';
import { WorkspaceEmptyState } from '../workspace/WorkspacePrimitives';
import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';
import { useExecutionSources, type ExecutionEdge, type ExecutionNode, type ExecutionSourceHost, type ExecutionSnapshot } from '../plugins/executionSources';
import type { WorldCard } from '../types/world';
import { executionIndex, layoutGraph, laneMemory, nodeEdges, trackPath, isComplete, isPlanned, isInProgress } from './executionHistory';
import './executionGraph.css';

export function ExecutionGraphCard({ card }: { card: WorldCard }) {
  const { deployed } = useWorkspaceAccess();
  return <ExecutionGraphView api={useExecutionSources(card.id, deployed)} />;
}

/** Publish a coherent revision only after all its pages arrive. */
export async function readExecutionHistory(api: ExecutionSourceHost, source: string): Promise<ExecutionSnapshot> {
  const first = await api.read(source, { limit: 1000 });
  const nodes = [...first.nodes], edges = [...first.edges ?? []];
  let cursor = first.next_cursor;
  const seen = new Set<string>();
  while (cursor) {
    if (seen.has(cursor)) throw new Error('Execution history returned a repeated cursor');
    seen.add(cursor);
    const next = await api.read(source, { limit: 1000, cursor });
    if (next.history_id !== first.history_id || next.revision !== first.revision) throw new Error('Execution history changed while loading; refreshing');
    nodes.push(...next.nodes); edges.push(...next.edges ?? []); cursor = next.next_cursor;
  }
  return { ...first, nodes, edges: first.edges === undefined ? undefined : edges, next_cursor: null };
}

/** Connected Agents share one surface, but retain source-local identities and
 * authorization. No relationship is inferred between independent providers. */
export async function readProjectHistory(api: ExecutionSourceHost, sources: { id: string; name: string }[], source = ''): Promise<ExecutionSnapshot> {
  const selected = source ? sources.filter(item => item.id === source) : sources;
  if (selected.length === 1) return readExecutionHistory(api, selected[0].id);
  const histories = await Promise.all(selected.map(async owner => ({ owner, value: await readExecutionHistory(api, owner.id) })));
  const identity = (source: string, id: string) => JSON.stringify([source, id]);
  return {
    title: t('Connected agents'), phase: histories.some(({ value }) => ['running', 'waiting'].includes(value.phase)) ? 'running' : 'idle',
    history_id: JSON.stringify(histories.map(({ owner, value }) => [owner.id, value.history_id || value.previous_run_id || value.run_id])),
    revision: JSON.stringify(histories.map(({ value }) => value.revision)),
    truncated: histories.some(({ value }) => value.truncated),
    nodes: histories.flatMap(({ owner, value }) => value.nodes.map(node => ({ ...node,
      id: identity(owner.id, node.id), parent_id: node.parent_id ? identity(owner.id, node.parent_id) : undefined,
      execution_id: node.execution_id ? identity(owner.id, node.execution_id) : undefined,
      actor_id: identity(owner.id, node.actor_id || owner.id), actor_label: node.actor_label || owner.name,
      workstream_id: node.workstream_id ? identity(owner.id, node.workstream_id) : undefined,
      realized_by: node.realized_by ? identity(owner.id, node.realized_by) : undefined,
      retry_of: node.retry_of ? identity(owner.id, node.retry_of) : undefined,
      replan_of: node.replan_of ? identity(owner.id, node.replan_of) : undefined,
      dependency_ids: node.dependency_ids?.map(id => identity(owner.id, id)),
    }))),
    edges: histories.flatMap(({ owner, value }) => executionIndex(value).rawEdges.map(edge => ({ ...edge,
      id: identity(owner.id, edge.id), source: identity(owner.id, edge.source), target: identity(owner.id, edge.target),
    }))),
  };
}
const edgeLabel = (kind: ExecutionEdge['kind'], incoming = true) => incoming ? ({
  control: t('Requires'), data: t('Uses result'), retry: t('Retry'), replan: t('Replan'),
  fork: t('Started from'), merge: t('Applied result'),
})[kind] : ({ control: t('Required by'), data: t('Used by'), retry: t('Retried as'),
  replan: t('Replanned as'), fork: t('Launched task'), merge: t('Applied at') })[kind];
function Status({ status }: { status: string }) {
  const Icon = isComplete(status) ? Check : ['failed', 'error', 'blocked', 'cancelled'].includes(status) ? X
    : status === 'running' ? LoaderCircle : ['waiting', 'interrupted', 'needs_replanning'].includes(status) ? Clock3 : Circle;
  return <span className="execution-status" data-status={status} title={t(status)}><Icon size={12} aria-hidden="true" /></span>;
}
function Operation({ node }: { node: ExecutionNode }) {
  const Icon = node.operation === 'edit' ? FileCode2 : node.operation === 'merge' ? GitMerge
    : node.operation === 'plan' || node.role === 'planning' ? GitBranch
    : node.kind === 'tool' ? Wrench : Play;
  return <Icon size={12} aria-hidden="true" />;
}
const timeText = (value?: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '';

function ToolSummary({ nodes }: { nodes: ExecutionNode[] }) {
  const groups = new Map<string, { node: ExecutionNode; count: number; notes: Set<string> }>();
  for (const node of nodes) {
    const key = JSON.stringify([node.label, node.status]);
    const group = groups.get(key) ?? { node, count: 0, notes: new Set<string>() };
    group.count++;
    for (const note of [node.result, node.detail, ...node.artifact_refs ?? []]) if (note) group.notes.add(note);
    groups.set(key, group);
  }
  return <div className="execution-task-tools"><small>{t('Tool activity')} · {nodes.length}</small>
    <ul>{[...groups].map(([key, { node, count, notes }]) => <li key={key}>
      <Status status={node.status} /><span>{node.label}</span>{count > 1 && <small>×{count}</small>}
      {!!notes.size && <div className="execution-tool-notes">{[...notes].map(note => <p key={note}>{note}</p>)}</div>}
    </li>)}</ul>
  </div>;
}

export function ExecutionGraphView({ api }: { api?: ExecutionSourceHost }) {
  useLocale();
  const [value, setValue] = useState<ExecutionSnapshot>();
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<string>();
  const [hovered, setHovered] = useState<string>();
  const [sources, setSources] = useState<{ id: string; name: string }[]>([]);
  const [source, setSource] = useState('');
  const [query, setQuery] = useState('');
  const [match, setMatch] = useState(0);
  const [newCount, setNewCount] = useState(0);
  const [detailHeight, setDetailHeight] = useState(150);
  const [scroll, setScroll] = useState({ top: 0, height: 560, width: 500 });
  const memory = useRef(laneMemory());
  const viewport = useRef<HTMLDivElement>(null);
  const [information, setInformation] = useState<HTMLDivElement | null>(null);
  const gesture = useRef({ x: 0, y: 0, moved: false });
  const pendingNavigation = useRef<string>();
  const index = useMemo(() => executionIndex(value), [value]);
  const graph = useMemo(() => layoutGraph(index, memory.current, selected, detailHeight, query), [index, selected, detailHeight, query]);
  const priorRows = useRef(graph.rows);
  const known = useRef(new Set<string>());

  useEffect(() => {
    let stopped = false, generation = 0, reading = false, queued = false;
    let historyId: string | undefined, initialized = false;
    let timer: ReturnType<typeof setTimeout>;
    const reset = () => {
      setValue(undefined); setError(''); setSelected(undefined); setHovered(undefined); setSources([]);
      setQuery(''); setMatch(0); setNewCount(0); known.current = new Set();
      pendingNavigation.current = undefined; memory.current = laneMemory(); priorRows.current = [];
      if (viewport.current) viewport.current.scrollTop = 0;
    };
    reset();
    const refresh = async () => {
      if (reading) { queued = true; return; }
      reading = true; queued = false;
      const current = generation;
      clearTimeout(timer);
      try {
        const connected = await api!.list();
        if (stopped || current !== generation) return;
        setSources(connected.sources);
        if (!connected.sources.length) { reset(); return; }
        const target = connected.sources.some(item => item.id === source) ? source : '';
        const result = await readProjectHistory(api!, connected.sources, target);
        if (stopped || current !== generation) return;
        const identity = result.history_id || result.previous_run_id || result.run_id;
        if (!initialized || historyId !== identity) {
          setSelected(undefined); memory.current = laneMemory(); priorRows.current = [];
          setNewCount(0); setQuery(''); setMatch(0); known.current = new Set();
          if (viewport.current) viewport.current.scrollTop = 0;
        } else {
          const added = executionIndex(result).activities.filter(node => !known.current.has(node.id)).length;
          if (added) setNewCount(count => count + added);
        }
        initialized = true; historyId = identity;
        known.current = new Set(executionIndex(result).activities.map(node => node.id));
        setValue(result); setError('');
      } catch (reason) {
        if (!stopped && current === generation) {
          setError(String(reason));
          if (/\b(403|404)\b/.test(String(reason))) reset();
        }
      } finally {
        if (!stopped && current === generation) {
          reading = false;
          if (queued) void refresh(); else timer = setTimeout(() => void refresh(), 2000);
        }
      }
    };
    if (!api) return;
    void refresh();
    const unsubscribe = api.subscribe(reason => {
      if (reason !== 'execution') { generation++; reading = false; queued = false; historyId = undefined; initialized = false; reset(); }
      void refresh();
    });
    return () => { stopped = true; clearTimeout(timer); unsubscribe(); };
  }, [api, source]);

  useLayoutEffect(() => {
    const element = information;
    if (!element) return;
    const measure = () => setDetailHeight(element.offsetHeight);
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, [information, selected, value]);
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const measure = () => setScroll(previous => ({ ...previous, height: element.clientHeight || 560, width: element.clientWidth || 500 }));
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, []);
  // Preserve the event at the top of the reader's viewport, never chase live work.
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    const navigation = pendingNavigation.current;
    if (navigation) {
      const row = graph.ports.get(navigation);
      if (row) {
        if (row.top < element.scrollTop || row.top + row.height > element.scrollTop + element.clientHeight)
          element.scrollTop = Math.max(0, row.top - element.clientHeight / 4);
        pendingNavigation.current = undefined;
      }
    } else {
      const previous = priorRows.current.find(row => row.top + row.height > element.scrollTop);
      const current = previous && graph.ports.get(previous.id);
      if (current) element.scrollTop += current.top - previous!.top;
    }
    priorRows.current = graph.rows;
    setScroll(previous => ({ ...previous, top: element.scrollTop }));
  }, [graph]);
  useEffect(() => {
    if (!selected) return;
    if (!index.byId.has(selected)) setSelected(undefined);
    else if (index.eventId.get(selected) !== selected) {
      pendingNavigation.current = index.eventId.get(selected);
      setSelected(index.eventId.get(selected));
    }
  }, [selected, index]);

  const navigate = (id: string, toggle = false) => {
    if (!index.byId.has(id)) return;
    pendingNavigation.current = id;
    setSelected(current => toggle && current === id ? undefined : id);
  };
  const find = (next: number) => {
    if (!graph.matchIds.length) return;
    const position = (next + graph.matchIds.length) % graph.matchIds.length;
    setMatch(position); navigate(graph.matchIds[position]);
  };
  const related = nodeEdges(index, hovered ?? selected), selectedEdges = nodeEdges(index, selected);
  const relatedIds = new Set(related.map(edge => edge.id));
  const relatedNodes = new Set(related.flatMap(edge => [edge.source, edge.target]));
  const narrow = scroll.width < 380, gap = narrow ? 18 : scroll.width > 700 ? 30 : 24;
  const laneX = (lane: number) => 24 + lane * gap;
  const labelX = laneX(graph.lanes - 1) + (narrow ? 22 : 28);
  const firstY = Math.max(0, scroll.top - 108), lastY = Math.min(graph.height, scroll.top + scroll.height + 108);
  const visibleRows = graph.rows.filter(row => row.top + row.height >= firstY && row.top <= lastY);
  const visibleTracks = graph.tracks.filter(track => Math.max(track.source.y, track.target.y) >= firstY && Math.min(track.source.y, track.target.y) <= lastY);
  const active = selected ? index.byId.get(selected) : undefined;
  const taskTools = active ? index.details.get(active.id) ?? [] : [];
  const plans = active ? index.nodes.filter(node => isPlanned(node) && node.parent_id === active.id) : [];
  const referenceCount = new Map<string, number>();
  for (const track of graph.tracks) if (track.edge && track.reference) referenceCount.set(track.target.id, (referenceCount.get(track.target.id) ?? 0) + 1);
  for (const edge of graph.unavailable) referenceCount.set(edge.target, (referenceCount.get(edge.target) ?? 0) + 1);

  return <section className="execution-graph nodrag nopan nowheel" aria-label={t('Execution graph')} data-narrow={narrow}
    style={{ '--graph-label-x': labelX + 'px' } as CSSProperties}>
    <header className="execution-graph-heading"><strong><GitBranch size={16} />{t('Project history')}</strong>
      <span className="execution-phase" role="status" data-phase={value?.phase}>{['running', 'waiting'].includes(value?.phase ?? '') ? t(value!.phase) : ''}</span>
    </header>
    {value?.title && <p className="execution-project-title" title={value.title}>{value.title}</p>}
    {sources.length > 1 && <select className="execution-graph-source" aria-label={t('Execution source')} value={sources.some(item => item.id === source) ? source : ''} onChange={event => setSource(event.target.value)}>
      <option value="">{t('All connected agents')}</option>
      {sources.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>}
    {error && <p className="execution-graph-notice" role="alert">{error}</p>}
    {value?.truncated && <p className="execution-graph-notice">{t('Some history is unavailable from this source.')}</p>}
    {value?.previous_run_id && <p className="execution-graph-notice" role="status">{['created', 'running', 'waiting'].includes(value.phase) ? t('Waiting for this run’s graph…') : t('Showing the previous execution')}</p>}
    {graph.rows.length > 0 && <div className="execution-history-toolbar">
      <span>{graph.rows.length} {t('events')} <span aria-hidden="true">↓</span></span>
      <form onSubmit={event => { event.preventDefault(); find(match + 1); }}><Search size={12} />
        <input aria-label={t('Find event')} placeholder={t('Find event')} value={query}
          onChange={event => { setQuery(event.target.value); setMatch(-1); }} onKeyDown={event => { if (event.key === 'Escape') { setQuery(''); setSelected(undefined); } }} />
        {query && <button type="submit" aria-label={t('Next match')}>{graph.matchIds.length ? Math.max(0, match + 1) + '/' + graph.matchIds.length : '0'}<ChevronDown size={12} /></button>}
      </form>
    </div>}
    <div className="execution-graph-viewport" ref={viewport} tabIndex={0} aria-label={t('Execution history')}
      onPointerDownCapture={event => { gesture.current = { x: event.clientX, y: event.clientY, moved: false }; }}
      onPointerMoveCapture={event => { if (Math.hypot(event.clientX - gesture.current.x, event.clientY - gesture.current.y) > 4) gesture.current.moved = true; }}
      onClickCapture={event => { if (event.detail && gesture.current.moved) { event.preventDefault(); event.stopPropagation(); } }}
      onKeyDown={event => { if (event.key === 'Escape') { setSelected(undefined); event.stopPropagation(); } }}
      onClick={event => { if (event.target === event.currentTarget || (event.target as HTMLElement).classList?.contains('execution-graph-content')) setSelected(undefined); }}
      onScroll={event => {
        const element = event.currentTarget; setScroll(previous => ({ ...previous, top: element.scrollTop }));
        if (element.scrollTop + element.clientHeight >= element.scrollHeight - 8) setNewCount(0);
      }}>
      {!graph.rows.length ? <WorkspaceEmptyState title={t('No execution yet')} description={sources.length ? t('No runtime activity for this session') : t('Connect an Agent or a callable library to observe its execution in this session.')} /> :
      <div className="execution-graph-content" style={{ height: graph.height }}>
        <svg className="execution-tracks" width={labelX} height={Math.max(0, lastY - firstY)} viewBox={'0 ' + firstY + ' ' + labelX + ' ' + Math.max(1, lastY - firstY)} style={{ top: firstY }} aria-label={t('Recorded dependency tracks')}>
          {visibleTracks.map(track => {
            const highlighted = !!track.edge && relatedIds.has(track.id);
            return <path key={track.id} className={'execution-track execution-color-' + track.color}
              data-graph-edge={track.edge ? track.edge.source + ':' + track.edge.target : undefined}
              data-kind={track.kind} data-reference={track.reference} data-highlighted={highlighted}
              data-muted={!!(hovered ?? selected) && !highlighted && track.source.id !== selected && track.target.id !== selected}
              d={trackPath(track, laneX, highlighted)}>
              <title>{track.edge ? edgeLabel(track.edge.kind) : track.kind === 'workstream' ? t('Task activity') : t('Time order')}: {track.source.node.label} → {track.target.node.label}</title>
            </path>;
          })}
        </svg>
        {visibleRows.map(row => {
          const node = row.node, chosen = row.id === selected;
          return <div key={row.id} className="execution-vertex" data-row-id={row.id} data-selected={chosen} data-related={relatedNodes.has(row.id)} data-pending={isInProgress(node.status)}
            style={{ top: row.top, height: row.height }}>
            <button className="execution-node-select" aria-label={node.label + ': ' + node.status} aria-pressed={chosen} aria-expanded={chosen}
              data-simple={!node.actor_label && !node.workstream_label && !node.operation && !node.started_at && !referenceCount.get(row.id)}
              onClick={() => navigate(row.id, true)} onMouseEnter={() => setHovered(row.id)} onMouseLeave={() => setHovered(undefined)}>
              <span className={'execution-vertex-dot execution-color-' + row.color} data-lane={row.lane} style={{ left: laneX(row.lane) }}><i /></span>
              <span className="execution-vertex-label">
                <span className="execution-operation-title"><strong title={node.label}>{node.label}</strong><Status status={node.status} /></span>
                <span className="execution-operation-meta"><Operation node={node} /><span>{node.actor_label ? t(node.actor_label) : node.workstream_label || (node.role === 'planning' ? t('Planner') : '')}</span>
                  {timeText(node.started_at) && <time dateTime={node.started_at!}>{timeText(node.started_at)}</time>}
                  {!!referenceCount.get(row.id) && <span className="execution-reference-count">↖ {referenceCount.get(row.id)}</span>}
                </span>
              </span>
            </button>
            {chosen && <div ref={setInformation} className="execution-inline" aria-label={t('Event details')}>
              <p className="execution-detail-status"><Status status={node.status} />{t(node.status)}
                {node.started_at && <time>{new Date(node.started_at).toLocaleString()}
                  {node.finished_at && <> → {new Date(node.finished_at).toLocaleString()}</>}</time>}</p>
              {node.result && <p>{node.result}</p>}{node.detail && node.detail !== node.result && <p>{node.detail}</p>}
              {!!selectedEdges.length && <div className="execution-anchors">{selectedEdges.map(edge => {
                const incoming = edge.target === row.id, id = incoming ? edge.source : edge.target, other = index.byId.get(id);
                return <button key={edge.id} disabled={!graph.ports.has(id)} onClick={() => navigate(id)}
                  aria-label={edgeLabel(edge.kind, incoming) + ' ' + (other?.label ?? id)}>
                  {incoming ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
                  <span><small>{edgeLabel(edge.kind, incoming)}</small>{other?.label ?? id}
                    {edge.records.some(record => record.source !== edge.source || record.target !== edge.target) &&
                      <small className="execution-edge-evidence">{[...new Set(edge.records.map(record =>
                        (index.byId.get(record.source)?.label ?? record.source) + ' → ' + (index.byId.get(record.target)?.label ?? record.target)))].join('; ')}</small>}
                  </span>
                  {!other && <small>{t('Unavailable')}</small>}
                </button>;
              })}</div>}
              {plans.length > 0 && <div className="execution-planned"><small>{t('Planned work')}</small>{plans.map(plan =>
                <p key={plan.id}><Status status={plan.realized_by ? index.byId.get(plan.realized_by)?.status ?? plan.status : plan.status} />
                  {plan.realized_by && graph.ports.has(plan.realized_by) ? <button onClick={() => navigate(plan.realized_by!)}>{plan.label}<ArrowUpRight size={11} /></button> : plan.label}</p>)}</div>}
              {!!node.artifact_refs?.length && <div className="execution-artifacts"><small>{t('Output artifacts')}</small>{node.artifact_refs.map(ref => <p key={ref}>{ref}</p>)}</div>}
              {taskTools.length > 0 && <ToolSummary nodes={taskTools} />}
              {!node.detail && !node.result && !node.artifact_refs?.length && !selectedEdges.length && !plans.length && !taskTools.length && <p className="execution-empty-detail">{t('No additional details')}</p>}
            </div>}
          </div>;
        })}
      </div>}
      {!!graph.rows.length && <footer className="execution-graph-legend">
        {graph.tracks.some(track => track.kind === 'workstream') && <span><i data-kind="workstream" />{t('Task activity')}</span>}
        {index.edges.length > 0 && <span><i />{t('Dependency')}</span>}
        {graph.tracks.some(track => track.kind === 'history') && <span><i data-kind="history" />{t('Time order')}</span>}
      </footer>}
    </div>
    {newCount > 0 && <button className="execution-new-activity" onClick={() => {
      pendingNavigation.current = graph.rows.at(-1)?.id; setSelected(graph.rows.at(-1)?.id); setNewCount(0);
    }}><ArrowDown size={13} />{t('{count} new events', { count: newCount })}</button>}
  </section>;
}
