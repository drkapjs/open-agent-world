import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ComponentType } from 'react';
import { Check, Code2, Download, Eye, FileCode2, FolderOpen, Layers, Package, Plus, RefreshCw, Save } from 'lucide-react';
import { ApiError, apiErrorMessage, worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { exportedPackFile, MarketplacePublish, PACK_ID_PATTERN } from '../shell/PackPublishing';
import type { PluginSlot } from '../plugins/sdk';
import type { NodeSurfaceLevel, WorldCard } from '../types/world';
import { AuthoringPreview } from './AuthoringCardView';
import { FaceDesignerCanvas } from './FaceDesignerCanvas';
import { FunctionControls } from './FunctionControls';
import { faceStudio, FACE_MODES } from './faceDesign';
import { faceButtonTargets } from './faceButtons';
import type { CardInterface, PackProject, ProjectCard, ProjectCheck, ProjectList, ProjectTextFile } from './projectTypes';
import './packIDE.css';

type Buffer = Omit<ProjectTextFile, 'sha256'> & { original: string; sha256: string | null };
type Mode = 'design' | 'function' | 'code';
type EditorDraft = { project?: PackProject; buffers: Record<string, Buffer>; pending: Record<string, CardInterface>; selected: string; filePath: string; mode: Mode };
const emptyList: ProjectList = { projects: [], default_directory: '' };
const format = (value: unknown) => `${JSON.stringify(value, null, 2)}\n`;
// Installed preview is a host surface. Keep its optional plugin implementations
// out of the exported Pack SDK declaration graph.
const hostSurfaces = import.meta.glob<{ PluginSurface: ComponentType<{ card: WorldCard; slot: PluginSlot; level: NodeSurfaceLevel }> }>('../plugins/PluginSurface.tsx');
const InstalledCardSurface = lazy(async () => ({ default: (await hostSurfaces['../plugins/PluginSurface.tsx']()).PluginSurface }));

export function initialInterface(card: ProjectCard): CardInterface {
  const face: CardInterface['face'] = { title: card.label || card.id, description: '', variant: 'text', tone: 'sage', color: '#617b72',
    icon: 'sparkles', finish: 'normal', layout: 'stack', help_text: '', button_label: 'Run' };
  return { face: { ...face, studio: faceStudio(face) }, function: { fields: [], operation: 'template', template: '', separator: '\n' },
    action: card.actions?.[0]?.id ?? 'run', result_key: 'result', surfaces: card.kind === 'declarative' ? ['preview', 'body', 'settings', 'workspace'] : [] };
}

function download(file: File) {
  const url = URL.createObjectURL(file), anchor = document.createElement('a');
  anchor.href = url; anchor.download = file.name; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function PackIDE({ onDirtyChange, draftKey }: { onDirtyChange?: (dirty: boolean) => void; draftKey?: string }) {
  const [initial] = useState<Partial<EditorDraft>>(() => {
    const saved = draftKey && useNodeSurfaceStore.getState().drafts[draftKey];
    return saved ? JSON.parse(saved) as EditorDraft : {};
  });
  const [project, setProject] = useState<PackProject | undefined>(initial.project), [listing, setListing] = useState<ProjectList>(emptyList);
  const [buffers, setBuffers] = useState<Record<string, Buffer>>(initial.buffers ?? {}), [pending, setPending] = useState<Record<string, CardInterface>>(initial.pending ?? {});
  const [selected, setSelected] = useState(initial.selected ?? ''), [filePath, setFilePath] = useState(initial.filePath ?? ''), [mode, setMode] = useState<Mode>(initial.mode ?? 'design');
  const [name, setName] = useState('My Pack'), [distributionId, setDistributionId] = useState('local.my-pack'), [path, setPath] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [external, setExternal] = useState<PackProject>(), [check, setCheck] = useState<ProjectCheck>(), [artifact, setArtifact] = useState<File>();
  const [preview, setPreview] = useState(false), [level, setLevel] = useState<NodeSurfaceLevel>('inspector');
  const [directory, setDirectory] = useState(''), [newCardName, setNewCardName] = useState(''), [legacyId, setLegacyId] = useState('');
  const [newFile, setNewFile] = useState(''), [backendWheel, setBackendWheel] = useState(''), [frontendDir, setFrontendDir] = useState('');
  const [runtimePreview, setRuntimePreview] = useState(false);
  const [releaseVersion, setReleaseVersion] = useState(initial.project?.version ?? '');
  const [diskContent, setDiskContent] = useState<Record<string, string>>({});
  const active = useRef(false), latest = useRef({ project, buffers, pending, selected, filePath, mode }); latest.current = { project, buffers, pending, selected, filePath, mode };
  useEffect(() => () => {
    if (draftKey) useNodeSurfaceStore.getState().setDraft(draftKey, JSON.stringify(latest.current));
  }, [draftKey]);
  const dirty = Object.values(buffers).some(buffer => buffer.content !== buffer.original) || Object.keys(pending).length > 0;
  const cards = useWorldStore(state => state.cards);
  const legacyCards = cards.filter(card => card.config.face && card.config.function);
  const selectedCard = project?.cards.find(card => card.id === selected) ?? project?.cards[0];
  const installedCard = cards.find(card => card.type === selectedCard?.id);
  let design = selectedCard?.design_path && buffers[selectedCard.design_path] ? undefined : selectedCard?.design ?? undefined;
  let designError = '';
  if (selectedCard?.design_path && buffers[selectedCard.design_path]) {
    try { const raw = JSON.parse(buffers[selectedCard.design_path].content) as CardInterface; const base = selectedCard.design ?? initialInterface(selectedCard); design = { ...base, ...raw, face: { ...base.face, ...raw.face }, function: { ...base.function, ...raw.function } }; }
    catch { designError = 'The visual resource contains invalid JSON. Correct it in Code mode.'; }
  }
  if (selectedCard && pending[selectedCard.id]) design = pending[selectedCard.id];
  if (design && (!design.face || !design.function || !Array.isArray(design.function.fields) || !Array.isArray(design.surfaces) || (design.face.studio && (!design.face.studio.enabled?.length || !design.face.studio.modes || !design.face.studio.modes[design.face.studio.initial])))) {
    design = undefined; designError = 'The visual resource needs face, function, and surfaces. Correct it in Code mode.';
  }
  if (design) {
    try {
      const studio = faceStudio(design.face);
      if (studio.enabled.some(item => {
        const surface = studio.modes[item];
        return !surface || !Number.isFinite(surface.width) || !Number.isFinite(surface.height) || !Array.isArray(surface.shapes) || !surface.shapes.length || !Array.isArray(surface.elements);
      })) throw new Error('Invalid surface');
    } catch { design = undefined; designError = 'The visual resource contains an invalid surface. Correct it in Code mode, then run Check.'; }
  }
  const perform = async (work: () => Promise<void>) => {
    if (active.current) return;
    active.current = true; setBusy(true); setError(''); setNotice('');
    try { await work(); } catch (cause) {
      setError(apiErrorMessage(cause));
      if (cause instanceof ApiError && cause.status === 409 && latest.current.project) {
        try { setExternal(await worldApi.getPackProject(latest.current.project.id)); } catch { /* Keep the original conflict. */ }
      }
    } finally { active.current = false; setBusy(false); }
  };
  const accept = (next: PackProject, clear = false) => {
    setProject(next); setExternal(undefined); setArtifact(undefined); setCheck(undefined);
    setReleaseVersion(next.version);
    if (clear) { setBuffers({}); setPending({}); setSelected(next.cards[0]?.id ?? ''); setFilePath(''); setPreview(false); }
  };
  useEffect(() => { onDirtyChange?.(dirty); return () => onDirtyChange?.(false); }, [dirty, onDirtyChange]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);
  useEffect(() => { void worldApi.listPackProjects().then(setListing).catch(cause => setError(apiErrorMessage(cause))); }, []);
  const refresh = useCallback(async (force = false) => {
    const current = latest.current;
    if (!current.project || (active.current && !force)) return;
    const next = await worldApi.getPackProject(current.project.id);
    if (latest.current.project?.id !== next.id || next.revision === latest.current.project?.revision) return;
    setArtifact(undefined); setCheck(undefined);
    if (Object.values(latest.current.buffers).some(buffer => buffer.content !== buffer.original) || Object.keys(latest.current.pending).length) setExternal(next);
    else { setProject(next); setBuffers({}); setExternal(undefined); setNotice('External changes loaded.'); }
  }, []);
  useEffect(() => {
    if (!project) return;
    const update = () => { void refresh().catch(cause => setError(apiErrorMessage(cause))); };
    window.addEventListener('focus', update); const timer = window.setInterval(update, 5000);
    return () => { window.removeEventListener('focus', update); window.clearInterval(timer); };
  }, [project?.id, refresh]);
  useEffect(() => {
    if (!project || !filePath || buffers[filePath]) return;
    let current = true;
    void worldApi.readPackProjectFile(project.id, filePath).then(file => {
      if (current) setBuffers(existing => existing[file.path] ? existing : { ...existing, [file.path]: { ...file, original: file.content } });
    }).catch(cause => { if (current) setError(apiErrorMessage(cause)); });
    return () => { current = false; };
  }, [project?.id, project?.revision, filePath, buffers]);
  const invalidate = () => { setCheck(undefined); setArtifact(undefined); };
  const changeDesign = (next: CardInterface) => {
    if (!selectedCard || !project) return;
    invalidate();
    setPending(current => ({ ...current, [selectedCard.id]: next }));
    const resource = selectedCard.design_path;
    if (!resource) return;
    setBuffers(current => ({ ...current, [resource]: { path: resource, sha256: current[resource]?.sha256 ?? project.files.find(file => file.path === resource)?.sha256 ?? null,
      original: current[resource]?.original ?? format(selectedCard.design), content: format(next) } }));
  };
  const save = async () => {
    if (!project) return;
    if (external) throw new Error('Review external changes before saving. Your draft is still available below.');
    let current = project;
    const visualPaths = new Set(project.cards.filter(card => pending[card.id]).map(card => card.design_path));
    for (const [cardId, value] of Object.entries(pending)) {
      current = await worldApi.savePackProjectDesign(current.id, { card_id: cardId, design: value, expected_revision: current.revision });
      setProject(current);
      const designPath = current.cards.find(card => card.id === cardId)?.design_path;
      if (designPath) {
        const file = await worldApi.readPackProjectFile(current.id, designPath);
        setBuffers(previous => ({ ...previous, [designPath]: { ...file, original: file.content } }));
      }
      setPending(previous => { const next = { ...previous }; delete next[cardId]; return next; });
    }
    for (const buffer of Object.values(buffers).filter(value => value.content !== value.original && !visualPaths.has(value.path))) {
      const saved = await worldApi.writePackProjectFile(current.id, { path: buffer.path, content: buffer.content, expected_sha256: buffer.sha256 });
      setBuffers(previous => ({ ...previous, [saved.path]: { ...saved, original: saved.content } }));
    }
    accept(await worldApi.getPackProject(current.id)); setNotice('Project files saved.');
  };
  const switchProject = (next: PackProject) => accept(next, true);
  const maySwitch = () => !dirty || window.confirm('Discard your unsaved project edits?');
  const selectFile = (value: string) => { setFilePath(value); setMode('code'); setPreview(false); };
  const runCheck = async () => {
    if (!project) return;
    const result = await worldApi.checkPackProject(project.id, project.revision); setCheck(result);
    setNotice(result.ok ? 'Static project check passed. Build and installed runtime verification are separate steps.' : 'Resolve the diagnostics before building.');
  };
  const currentBuffer = buffers[filePath];
  return <section className="factory pack-ide nodrag nopan nowheel" aria-label="Pack IDE" aria-busy={busy}>
    <fieldset className="pack-ide-guard" disabled={busy}><header className="pack-ide-header"><div className="pack-ide-brand"><Package size={19} /><div><h2>{project?.name ?? 'Pack IDE'}</h2>
      <span>{project ? `${project.distribution_id} · ${project.version}` : 'Design, code, and build one Pack project'}</span></div></div>
      {project && <div className="pack-ide-toolbar">
        <button disabled={busy || !dirty || !!external} onClick={() => void perform(save)}><Save size={14} />Save</button>
        <button disabled={busy || dirty || !!external} onClick={() => void perform(runCheck)}><Check size={14} />Check</button>
        <button disabled={busy} aria-pressed={preview} onClick={() => setPreview(!preview)}><Eye size={14} />Preview</button>
        <button className="pack-ide-primary" disabled={busy || dirty || !!external || check?.ok !== true} onClick={() => void perform(async () => {
          const blob = await worldApi.buildPackProject(project.id, { expected_revision: project.revision, ...(backendWheel ? { backend_wheel: backendWheel } : {}), ...(frontendDir ? { frontend_dir: frontendDir } : {}) });
          const file = exportedPackFile(blob, { id: project.distribution_id, version: project.version }); download(file); setArtifact(file);
          setNotice('Schema 3 archive built and downloaded. Install it through the Pack Library to verify runtime behavior.');
        })}><Download size={14} />Build / Export</button>
      </div>}
    </header>
    {error && <p className="pack-ide-message factory-error" role="alert">{error}</p>}
    {notice && <p className="pack-ide-message" role="status">{notice}</p>}
    {external && <div className="pack-ide-conflict" role="alert"><strong>Files changed externally</strong><p>Your draft is preserved. Compare it with disk before reloading; saves and builds are paused.</p>
      <details><summary>Review disk changes</summary>{external.files.filter(file => project?.files.find(old => old.path === file.path)?.sha256 !== file.sha256).map(file => <div key={file.path}><code>{file.path}</code>{file.editable && <button onClick={() => void perform(async () => { const current = await worldApi.readPackProjectFile(external.id, file.path); setDiskContent(previous => ({ ...previous, [file.path]: current.content })); })}>Compare {file.path}</button>}{diskContent[file.path] !== undefined && <div className="pack-ide-diff"><pre aria-label={`Draft ${file.path}`}>{buffers[file.path]?.content ?? '(no local edits)'}</pre><pre aria-label={`Disk ${file.path}`}>{diskContent[file.path]}</pre></div>}</div>)}{project?.files.filter(file => !external.files.some(current => current.path === file.path)).map(file => <p key={file.path}>Deleted: <code>{file.path}</code></p>)}</details>
      <button onClick={() => { if (window.confirm('Reload from disk and discard your unsaved edits?')) accept(external, true); }}>Reload from disk</button></div>}
    {!project ? <div className="pack-ide-welcome"><div><span className="pack-ide-eyebrow">ONE PROJECT · EVERY WORKFLOW</span><h3>Make a card. Grow it into a Pack.</h3><p>Start visually or open source from your editor. Designs, Python, and frontend code stay together in ordinary project files.</p></div>
      <div className="pack-ide-start-grid"><form onSubmit={event => { event.preventDefault(); void perform(async () => switchProject(await worldApi.createPackProject({ name, distribution_id: distributionId, ...(directory ? { directory } : {}), ...(legacyId ? { design: { ...initialInterface({ id: '', label: name, kind: 'declarative', design: null, design_path: null }), face: legacyCards.find(card => card.id === legacyId)!.config.face as CardInterface['face'], function: legacyCards.find(card => card.id === legacyId)!.config.function as CardInterface['function'] } } : {}) }))); }}>
        <h4><Plus size={16} />New project</h4><label>Project name<input value={name} required maxLength={120} onChange={event => setName(event.target.value)} /></label>
        <label>Pack ID<input value={distributionId} required pattern={PACK_ID_PATTERN} onChange={event => setDistributionId(event.target.value)} /></label><small>Use local.* for offline work, or your Marketplace namespace.</small>
        <details><summary>Project location & existing designs</summary><label>New project directory<input value={directory} placeholder="Optional absolute path to a new directory" onChange={event => setDirectory(event.target.value)} /></label><p>Choose a directory outside the OAW profile to share with an Agent through its existing workspace permissions.</p>
          {legacyCards.length > 0 && <label>Start from saved Factory card<select value={legacyId} onChange={event => setLegacyId(event.target.value)}><option value="">New card</option>{legacyCards.map(card => <option key={card.id} value={card.id}>{card.name}</option>)}</select><small>The original saved card stays intact. The new project builds as Schema 3.</small></label>}</details>
        <button className="pack-ide-primary" disabled={busy} type="submit">Create project</button></form>
        <form onSubmit={event => { event.preventDefault(); void perform(async () => switchProject(await worldApi.openPackProject(path))); }}><h4><FolderOpen size={16} />Existing source</h4>
          <label>Project path<input value={path} required placeholder="Folder containing oaw.pack.toml" onChange={event => setPath(event.target.value)} /></label>
          <p>Open the source directory used by VS Code, Codex, or another editor.</p><small>Compiled archives do not contain an editable source project.</small><button disabled={busy} type="submit">Open project</button></form></div>
      {listing.projects.length > 0 && <div className="pack-ide-recents"><h4>Recent projects</h4>{listing.projects.map(item => <button key={item.id} disabled={busy} onClick={() => void perform(async () => switchProject(await worldApi.getPackProject(item.id)))}><FolderOpen size={15} /><span>{item.name}<small>{item.path}</small></span></button>)}</div>}
      {listing.default_directory && <small>New projects: <code>{listing.default_directory}</code></small>}
    </div> : <>
      <div className="pack-ide-body"><aside className="pack-ide-tree"><div className="pack-ide-tree-heading"><strong>Project contents</strong><button title="Refresh project" aria-label="Refresh project" disabled={busy} onClick={() => void perform(() => refresh(true))}><RefreshCw size={13} /></button></div>
        <small className="pack-ide-path" title={project.path}>{project.path}</small><h4>Cards</h4>
        {project.cards.map(card => <button className="pack-ide-tree-item" aria-current={selectedCard?.id === card.id && mode !== 'code' ? 'true' : undefined} key={card.id} onClick={() => { setSelected(card.id); setMode('design'); setPreview(false); setRuntimePreview(false); }}><Layers size={14} /><span>{card.label || card.id}<small>{card.kind === 'python' ? 'Python' : 'Declarative'}</small></span></button>)}
        {project.cards.every(card => card.kind === 'declarative') && <details className="pack-ide-new-file"><summary>Add card</summary><label>Card name<input value={newCardName} onChange={event => setNewCardName(event.target.value)} /></label><button disabled={busy || dirty || !!external || !newCardName.trim()} onClick={() => void perform(async () => { const next = await worldApi.addPackProjectCard(project.id, { name: newCardName, expected_revision: project.revision }); accept(next); setSelected(next.cards.at(-1)?.id ?? ''); setNewCardName(''); setMode('design'); })}>Create card</button></details>}
        {Boolean(project.relationships?.length) && <><h4>Relationships</h4>{project.relationships!.map(relationship => <button className="pack-ide-tree-item" key={relationship.id} disabled={!relationship.source_path} onClick={() => { if (relationship.source_path) selectFile(relationship.source_path); }} title={`${relationship.source_types?.join(', ') || '*'} ? ${relationship.target_types?.join(', ') || '*'}`}><Layers size={13} /><span>{relationship.label || relationship.id}</span></button>)}</>}
        <h4>Source & resources</h4><div className="pack-ide-files">{[...project.files, ...Object.values(buffers).filter(file => !project.files.some(saved => saved.path === file.path)).map(file => ({ ...file, editable: true }))].map(file => <button className="pack-ide-tree-item" aria-current={mode === 'code' && filePath === file.path ? 'true' : undefined} disabled={!file.editable} key={file.path} onClick={() => selectFile(file.path)} title={file.path}><FileCode2 size={13} /><span>{file.path}{buffers[file.path]?.content !== undefined && buffers[file.path].content !== buffers[file.path].original ? ' •' : ''}</span></button>)}</div>
        <details className="pack-ide-new-file"><summary>Add source file</summary><label>Relative file path<input value={newFile} placeholder="src/analysis.py" onChange={event => setNewFile(event.target.value)} /></label><button disabled={!newFile || busy} onClick={() => {
          if (project.files.some(file => file.path === newFile) || buffers[newFile]) { setError('This file already exists. Select it from the tree.'); return; }
          setBuffers(current => ({ ...current, [newFile]: { path: newFile, content: '\n', original: '', sha256: null } })); selectFile(newFile); setNewFile(''); invalidate();
        }}>Add file</button></details>
        <button className="pack-ide-project-switch" disabled={busy} onClick={() => { if (maySwitch()) { setProject(undefined); setBuffers({}); setPending({}); setArtifact(undefined); } }}><FolderOpen size={14} />Create / Open</button>
      </aside><main className="pack-ide-editor"><div className="pack-ide-editor-heading"><div><strong>{mode === 'code' ? filePath || 'Source files' : selectedCard?.label || 'Select a card'}</strong><small>{dirty ? 'Unsaved changes' : 'Saved to project'}{selectedCard?.design_path && mode !== 'code' ? ` · ${selectedCard.design_path}` : ''}</small></div>
        <nav aria-label="Editing mode">{(['design', 'function', 'code'] as Mode[]).map(item => <button key={item} aria-pressed={mode === item} onClick={() => { setMode(item); setPreview(false); if (item === 'code' && !filePath) setFilePath(selectedCard?.design_path ?? 'oaw.pack.toml'); }}>{item === 'code' && <Code2 size={13} />}{item[0].toUpperCase() + item.slice(1)}</button>)}</nav></div>
        {preview ? <div className="pack-ide-preview"><div className="pack-ide-preview-toolbar"><strong>Card preview</strong><label>Display mode<select value={level} onChange={event => setLevel(event.target.value as NodeSurfaceLevel)}>{FACE_MODES.map(value => <option key={value}>{value}</option>)}</select></label>
          {installedCard && <label className="factory-check"><input type="checkbox" checked={runtimePreview} onChange={event => setRuntimePreview(event.target.checked)} />Use installed card</label>}</div>
          {runtimePreview && installedCard ? <><p>Installed runtime: {installedCard.name}. Actions affect this card; rebuild and restart to load updated code.</p><Suspense fallback={<p role="status">Loading installed card…</p>}><InstalledCardSurface card={installedCard} slot={level === 'inspector' ? 'body' : level === 'node' ? 'preview' : level} level={level} /></Suspense></>
            : design ? <><p>{selectedCard?.kind === 'declarative' ? 'Visual proof with the built-in function evaluator. Build and install to verify the Pack.' : 'Visual proof only. Python and custom frontend code run after installation in a test profile.'}</p>
              <div className="pack-ide-preview-scroll"><AuthoringPreview design={design} level={level} run={selectedCard?.kind === 'declarative' ? async (action, values) => { if (action !== design.action) throw new Error('This action is not implemented by the built-in function. Check the project binding.'); return (await worldApi.previewPackProject(project.id, design.function, values)).result; } : undefined} onNavigate={(_action, target) => setLevel(target ?? faceStudio(design.face).open)} /></div></>
              : <p>Custom frontend source is preserved. Build and install this Pack in a test profile to preview its actual component.</p>}</div>
          : mode === 'code' ? <div className="pack-ide-code"><p>Editing the actual source file. External editors and Agents use this same path.</p>{currentBuffer ? <><textarea spellCheck={false} aria-label="Source code" value={currentBuffer.content} onChange={event => { setBuffers(current => ({ ...current, [filePath]: { ...current[filePath], content: event.target.value } })); setPending(current => Object.fromEntries(Object.entries(current).filter(([id]) => project.cards.find(card => card.id === id)?.design_path !== filePath))); invalidate(); }} /><details><summary>Review edits against loaded source</summary><div className="pack-ide-diff"><pre aria-label="Original source">{currentBuffer.original || '(new file)'}</pre><pre aria-label="Edited source">{currentBuffer.content}</pre></div></details></> : <p>{filePath ? 'Loading source…' : 'Select a file from the project tree.'}</p>}</div>
            : selectedCard && design ? mode === 'design' ? <FaceDesignerCanvas key={selectedCard.id} face={design.face} functionDesign={design.function} onChange={patch => changeDesign({ ...design, face: { ...design.face, ...patch } })} />
              : <div className="pack-ide-function"><div><h4>Inputs & behavior</h4><FunctionControls value={design.function} fieldsOnly={selectedCard.kind === 'python'} onChange={patch => changeDesign({ ...design, function: { ...design.function, ...patch } })} /></div>
                <div className="pack-ide-bindings"><h4>Runtime bindings</h4><label>Primary action<input list="pack-project-actions" value={design.action} onChange={event => changeDesign({ ...design, action: event.target.value })} /></label><datalist id="pack-project-actions">{selectedCard.actions?.map(action => <option key={action.id} value={action.id}>{action.label}</option>)}</datalist>
                  <label>Result key<input value={design.result_key} onChange={event => changeDesign({ ...design, result_key: event.target.value })} /></label><p>Fields become action arguments. The result key selects the action response displayed on the card.</p>
                  {faceButtonTargets(design.face).filter(button => button.action === 'custom').map(button => <label key={`${button.mode}:${button.element_id}`}>{button.label} · {button.mode}<input list="pack-project-actions" value={design.function.button_bindings?.find(binding => binding.mode === button.mode && binding.element_id === button.element_id)?.logic_id ?? ''} onChange={event => changeDesign({ ...design, function: { ...design.function, button_bindings: [...(design.function.button_bindings ?? []).filter(binding => binding.mode !== button.mode || binding.element_id !== button.element_id), ...(event.target.value ? [{ mode: button.mode, element_id: button.element_id, logic_id: event.target.value }] : [])] } })} /></label>)}
                  <h4>Visual surfaces</h4><p>Choose where this design renders. Other custom components keep their existing implementation.</p>{(['preview', 'body', 'settings', 'workspace'] as PluginSlot[]).map(slot => <label className="factory-check" key={slot}><input type="checkbox" checked={design.surfaces.includes(slot)} onChange={event => changeDesign({ ...design, surfaces: event.target.checked ? [...design.surfaces, slot] : design.surfaces.filter(value => value !== slot) })} />{slot}{selectedCard.frontend?.[slot] ? ` · ${selectedCard.frontend[slot]}` : ''}</label>)}
                  {selectedCard.kind === 'python' && <p>Python registration, state, relationships, and execution remain in source. Validate dynamic registrations with the external check command or an installed test profile.</p>}</div></div>
              : <div className="pack-ide-empty"><Layers size={28} /><h3>{designError || 'Add a visual interface'}</h3><p>Keep the existing backend and custom frontend. Store appearance and action bindings in a separate editable resource.</p>
                {selectedCard && !designError && <button onClick={() => changeDesign(initialInterface(selectedCard))}><Plus size={14} />Add visual interface</button>}</div>}
      </main></div>
      <footer className="pack-ide-footer"><details open={Boolean(check && !check.ok)}><summary>Diagnostics {check ? `· ${check.ok ? 'Passed' : 'Needs attention'}` : ''}</summary>
        {(check?.diagnostics ?? project.diagnostics).map((item, index) => <p key={index} data-severity={item.severity}><code>{item.path}</code> {item.message}</p>)}
        {check?.ok && <p>Static declarations and resources are valid. No user-authored code was executed.</p>}</details>
        {project.cards.every(card => card.kind === 'declarative') && <details><summary>Project version</summary><div className="factory-row"><label>Release version<input value={releaseVersion} onChange={event => setReleaseVersion(event.target.value)} /></label><button disabled={busy || dirty || !!external || !releaseVersion || releaseVersion === project.version} onClick={() => void perform(async () => { accept(await worldApi.versionPackProject(project.id, { version: releaseVersion, expected_revision: project.revision })); setNotice('Version updated. Check and build a new archive.'); })}>Update version</button></div><p>Increase the version when updating an installed Pack.</p></details>}
        <details><summary>Build inputs & external development</summary><p>Use the documented project CLI for Python wheels and custom frontend builds. The IDE packages those prepared outputs through the same Schema 3 builder.</p>
          <div className="factory-row"><label>Backend wheel (optional)<input value={backendWheel} onChange={event => { setBackendWheel(event.target.value); setArtifact(undefined); }} placeholder="dist/my_pack-0.1.0-py3-none-any.whl" /></label><label>Frontend output (optional)<input value={frontendDir} onChange={event => { setFrontendDir(event.target.value); setArtifact(undefined); }} placeholder="dist/frontend" /></label></div>
          <code>python -m open_agent_world.pack check {JSON.stringify(project.path)}</code><p>For OAW Agents, choose a project outside the protected OAW profile and grant it through the existing workspace settings. Review file changes in Code mode.</p></details>
        <MarketplacePublish file={!dirty && !external ? artifact : undefined} packId={project.distribution_id} busy={busy} /></footer>
    </>}</fieldset>
  </section>;
}
