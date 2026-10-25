// @vitest-environment jsdom
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { PackIDE, initialInterface } from './PackIDE';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import type { PackProject, ProjectCard } from './projectTypes';

vi.mock('./FaceDesignerCanvas', () => ({ FaceDesignerCanvas: () => <p>Visual editor</p> }));
const card: ProjectCard = { id: 'team.example.card', label: 'Example', kind: 'declarative', design_path: 'designs/card.json', design: null };
const design = initialInterface(card);
design.function = { fields: [{ key: 'name', label: 'Name', type: 'text', default: 'World', required: false }], operation: 'template', template: 'Hello {{name}}', separator: '\n' };
const hash = 'a'.repeat(64);
const project: PackProject = { id: 'project', path: '/projects/example', name: 'Example', distribution_id: 'team.example', version: '0.1.0', revision: hash,
  files: [{ path: 'designs/card.json', size_bytes: 100, sha256: hash, editable: true }, { path: 'src/behavior.py', size_bytes: 20, sha256: hash, editable: true }],
  cards: [{ ...card, design }], spec: {}, diagnostics: [] };
beforeEach(() => {
  vi.spyOn(worldApi, 'listPackProjects').mockResolvedValue({ projects: [], default_directory: '/projects' });
  vi.spyOn(worldApi, 'createPackProject').mockResolvedValue(project);
  vi.spyOn(worldApi, 'getPackProject').mockResolvedValue(project);
  vi.spyOn(worldApi, 'checkPackProject').mockResolvedValue({ ok: true, diagnostics: [], level: 'static' });
  vi.spyOn(worldApi, 'readPackProjectFile').mockImplementation(async (_id, filePath) => ({ path: filePath,
    content: filePath.endsWith('.json') ? JSON.stringify(design, null, 2) + '\n' : 'handwritten = True\n', sha256: hash }));
});
afterEach(() => { cleanup(); useNodeSurfaceStore.getState().setDraft('pack-ide-test-draft', ''); vi.restoreAllMocks(); vi.unstubAllGlobals(); });
async function opened() {
  const screen = render(<PackIDE />);
  fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
  await screen.findByRole('button', { name: 'Function' });
  return screen;
}

it('saves validated visual logic with the loaded project revision, leaving handwritten source untouched', async () => {
  const write = vi.spyOn(worldApi, 'writePackProjectFile');
  const save = vi.spyOn(worldApi, 'savePackProjectDesign').mockResolvedValue(project);
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Function' }));
  fireEvent.change(screen.getByLabelText(/Output template/), { target: { value: 'Welcome {{name}}' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Project files saved.');
  expect(write).not.toHaveBeenCalled();
  expect(save).toHaveBeenCalledOnce();
  expect(save.mock.calls[0][1]).toMatchObject({ card_id: card.id, expected_revision: hash,
    design: { function: { template: 'Welcome {{name}}' } } });
});

it('keeps the visual draft editable when validation rejects a function', async () => {
  vi.spyOn(worldApi, 'savePackProjectDesign').mockRejectedValue(new Error('Sum requires a numeric field'));
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'Function' }));
  fireEvent.change(screen.getByLabelText('Operation'), { target: { value: 'sum' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await screen.findByText('Sum requires a numeric field');
  expect((screen.getByLabelText('Operation') as HTMLSelectElement).value).toBe('sum');
  expect(screen.getByLabelText('Field type')).toBeTruthy();
});

it('retains unsaved source edits when a Factory workspace closes and reopens', async () => {
  const screen = render(<PackIDE draftKey="pack-ide-test-draft" />);
  fireEvent.click(screen.getByRole('button', { name: 'Create project' }));
  fireEvent.click(await screen.findByRole('button', { name: 'src/behavior.py' }));
  fireEvent.change(await screen.findByRole('textbox', { name: 'Source code' }), { target: { value: 'retained_draft = True\n' } });
  screen.unmount();
  const reopened = render(<PackIDE draftKey="pack-ide-test-draft" />);
  expect((reopened.getByRole('textbox', { name: 'Source code' }) as HTMLTextAreaElement).value).toBe('retained_draft = True\n');
});

it('preserves a dirty source buffer when another editor changes disk and pauses saves', async () => {
  const write = vi.spyOn(worldApi, 'writePackProjectFile');
  const screen = await opened();
  fireEvent.click(screen.getByRole('button', { name: 'src/behavior.py' }));
  const editor = await screen.findByRole('textbox', { name: 'Source code' });
  fireEvent.change(editor, { target: { value: 'my_draft = True\n' } });
  vi.mocked(worldApi.getPackProject).mockResolvedValue({ ...project, revision: 'b'.repeat(64), files: project.files.map(file => ({ ...file, sha256: 'b'.repeat(64) })) });
  fireEvent(window, new Event('focus'));
  await screen.findByText('Files changed externally');
  expect((editor as HTMLTextAreaElement).value).toBe('my_draft = True\n');
  expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  expect(write).not.toHaveBeenCalled();
});

it('only offers the publishing handoff after a checked export and invalidates it on an edit', async () => {
  const build = vi.spyOn(worldApi, 'buildPackProject').mockResolvedValue(new Blob(['schema3'], { type: 'application/vnd.oaw.pack' }));
  vi.stubGlobal('URL', { createObjectURL: vi.fn(() => 'blob:pack'), revokeObjectURL: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const screen = await opened();
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Check' }));
  await waitFor(() => expect((screen.getByRole('button', { name: 'Build / Export' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.click(screen.getByRole('button', { name: 'Build / Export' }));
  await screen.findByRole('link', { name: 'Publish on Marketplace' });
  expect(build).toHaveBeenCalledOnce();
  expect(build).toHaveBeenCalledWith('project', { expected_revision: hash });
  fireEvent.click(screen.getByRole('button', { name: 'Function' }));
  fireEvent.change(screen.getByLabelText(/Output template/), { target: { value: 'Changed' } });
  expect(screen.queryByRole('link', { name: 'Publish on Marketplace' })).toBeNull();
});
