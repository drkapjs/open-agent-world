import { useEffect, useId, useMemo, useState } from 'react';
import { t, useLocale } from '../i18n';
import { Bot, Layers, Wrench } from 'lucide-react';
import { WorkspaceEmptyState } from '../workspace/WorkspacePrimitives';
import { useWorkspaceAccess } from '../workspace/WorkspaceAccess';
import { useExecutionSources, type ExecutionSourceHost, type ExecutionSnapshot } from '../plugins/executionSources';
import type { WorldCard } from '../types/world';
import { executionGraph } from './executionGraphLayout';
import { useExecutionGraphViewport } from './useExecutionGraphViewport';
import './executionGraph.css';

export function ExecutionGraphCard({ card }: { card: WorldCard }) {
  const { deployed } = useWorkspaceAccess();
  const api = useExecutionSources(card.id, deployed);
  return <ExecutionGraphView api={api} />;
}

export function ExecutionGraphView({ api }: { api?: ExecutionSourceHost }) {
  useLocale();
  const [value, setValue] = useState<ExecutionSnapshot>();
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'agents' | 'plan'>('agents');
  const [selected, setSelected] = useState<string>();
  const [sources, setSources] = useState<{ id: string; name: string }[]>([]);
  const [source, setSource] = useState('');
  const graph = useMemo(() => executionGraph(value, mode), [value, mode]);
  const { viewport, camera, panning, fit, reset, zoom, handlers } = useExecutionGraphViewport(graph.width, graph.height, graph.nodes.length > 0);
  const marker = `execution-arrow-${useId().replace(/:/g, '')}`;
  useEffect(() => {
    let stopped = false;
    let generation = 0;
    let reading = false;
    let queued = false;
    let runId: string | undefined;
    let timer: ReturnType<typeof setTimeout>;
    setValue(undefined); setError(''); setSelected(undefined); reset(); setSources([]);
    const refresh = async () => {
      if (reading) { queued = true; return; }
      reading = true;
      queued = false;
      const current = generation;
      clearTimeout(timer);
      try {
        const connected = await api!.list();
        if (stopped || current !== generation) return;
        setSources(connected.sources);
        const target = connected.sources.find(item => item.id === source)?.id ?? connected.sources[0]?.id;
        if (!target) { setValue(undefined); setSelected(undefined); setError(''); return; }
        const result = await api!.read(target);
        if (!stopped && current === generation) {
          const graphRunId = result.previous_run_id || result.run_id;
          if (runId !== graphRunId) { setSelected(undefined); reset(); }
          runId = graphRunId;
          setValue(result); setError('');
        }
      } catch (reason) { if (!stopped && current === generation) { setValue(undefined); setError(String(reason)); } }
      finally {
        if (!stopped && current === generation) {
          reading = false;
          if (queued) void refresh();
          else timer = setTimeout(() => void refresh(), 2000);
        }
      }
    };
    if (!api) return;
    void refresh();
    const unsubscribe = api.subscribe(reason => {
      if (reason !== 'execution') {
        // Connection revocation invalidates even in-flight reads immediately.
        generation++; reading = false; queued = false; runId = undefined;
        setValue(undefined); setSelected(undefined); setSources([]); reset();
      }
      void refresh();
    });
    return () => { stopped = true; clearTimeout(timer); unsubscribe(); };
  }, [api, source, reset]);
  const selectedNode = graph.nodes.find(node => node.id === selected);
  const positions = new Map(graph.nodes.map(node => [node.id, node]));
  return <section className="execution-graph nodrag nopan nowheel" aria-label={t('Execution graph')}>
    <header className="execution-graph-heading"><strong>{t('Execution')}</strong><span role="status" data-phase={value?.phase}>{value?.phase ?? (sources.length ? t('Loading…') : t('No source'))}</span></header>
    {sources.length > 0 && <select className="execution-graph-source" aria-label={t('Execution source')} value={sources.some(item => item.id === source) ? source : sources[0].id} onChange={event => setSource(event.target.value)}>
      {sources.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
    </select>}
    <div className="execution-graph-toolbar">
      <div role="group" aria-label={t('Execution graph view')}>
        {(['agents', 'plan'] as const).map(kind => <button key={kind} aria-pressed={mode === kind} onClick={() => { setMode(kind); setSelected(undefined); reset(); }}>{kind === 'agents' ? t('Agents') : t('Plan')}</button>)}
      </div>
      <button aria-label={t('Zoom out')} onClick={() => zoom(1 / 1.25)}>−</button>
      <button aria-label={t('Fit graph')} onClick={fit}>{t('Fit')}</button>
      <button aria-label={t('Zoom in')} onClick={() => zoom(1.25)}>+</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {!api && <p role="status">{t('Execution sources are available in the builder workspace.')}</p>}
    {value?.truncated && <p role="status">{t('Showing the latest 500 execution nodes.')}</p>}
    {value?.previous_run_id && <p className="execution-graph-notice" role="status"
      title={t('The previous execution remains visible until this run publishes a detailed graph.')}>
      {['created', 'running', 'waiting'].includes(value.phase) ? t('Waiting for this run’s graph…') : t('Showing the previous execution')}
    </p>}
    {value?.title && <details className="execution-graph-goal" key={`${source}:${value.previous_run_id || value.run_id || ''}`}>
      <summary>{value.previous_run_id ? t('Previous task goal') : t('Task goal')}</summary><p>{value.title}</p>
    </details>}
    <div className="execution-graph-viewport" ref={viewport} tabIndex={0} aria-label={t('Execution graph canvas')}
      title={t('Drag to pan · Scroll to zoom · Home to fit')} data-panning={panning} {...handlers}>
      {!graph.nodes.length ? <WorkspaceEmptyState title={t('No execution yet')} description={sources.length ? t('No runtime activity for this session') : t('Connect an Agent or a callable library to observe its execution in this session.')} /> :
        <div className="execution-graph-canvas" data-scale={camera.scale} style={{ width: graph.width, height: graph.height, transform: `translate(${camera.x}px, ${camera.y}px) scale(${camera.scale})` }}>
          <svg width="100%" height="100%" viewBox={`0 0 ${graph.width} ${graph.height}`} role="img" aria-label={mode === 'agents' ? t('Agent execution hierarchy') : t('Plan dependencies')}>
            <defs><marker id={marker} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="currentColor" /></marker></defs>
            {graph.edges.map(edge => {
              const from = positions.get(edge.source)!, to = positions.get(edge.target)!;
              const start = from.y + 66, end = to.y - 26, middle = (start + end) / 2;
              return <path key={`${edge.source}:${edge.target}:${edge.dependency}`} data-graph-edge={`${edge.source}:${edge.target}`} className={edge.dependency ? 'is-dependency' : ''}
                d={`M${from.x},${start} C${from.x},${middle} ${to.x},${middle} ${to.x},${end}`} markerEnd={`url(#${marker})`} />;
            })}
          </svg>
          {graph.nodes.map(node => <button key={node.id} className="execution-graph-node" data-status={node.status} aria-pressed={selected === node.id}
            aria-label={`${node.label}: ${node.status}`} title={`${node.label} · ${node.status}`} onClick={() => setSelected(selected === node.id ? undefined : node.id)}
            style={{ left: node.x, top: node.y - 24 }}>
            <span className="execution-graph-orb">{node.badge === 'plan' ? <Layers size={18} /> : node.badge === 'tool' ? <Wrench size={18} /> : <Bot size={18} />}<i aria-hidden="true" /></span><strong>{node.label}</strong><small>{node.status}</small>
          </button>)}
        </div>}
    </div>
    {selectedNode && <section className="execution-graph-detail" aria-label={t('Execution details')}>
      <header><strong>{selectedNode.label}</strong><button aria-label={t('Close details')} onClick={() => setSelected(undefined)}>×</button></header>
      <small>{selectedNode.id} · {selectedNode.status}</small>
      {selectedNode.detail && <p>{selectedNode.detail}</p>}
      {graph.edges.filter(edge => edge.target === selectedNode.id).map(edge => <p key={`${edge.source}:${edge.dependency}`}>{edge.dependency ? t('Depends on') : t('Called by')}: {positions.get(edge.source)?.label}</p>)}
      {selectedNode.result && <p>{selectedNode.result}</p>}
    </section>}
    <footer className="execution-graph-legend"><span data-status="running">{t('Running')}</span><span data-status="success">{t('Complete')}</span><span data-status="error">{t('Error')}</span></footer>
  </section>;
}
