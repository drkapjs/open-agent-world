// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ArtifactCollection, type ArtifactVersion } from './Artifacts';
import { worldApi } from '../api/client';
import { useWorldStore } from '../state/worldStore';
import { useLocale } from '../i18n';
import { buildCardDraft } from '../state/helpers';

const card = { id: 'outputs', ...buildCardDraft('core.artifact-collection', { x: 0, y: 0 }) };
const version: ArtifactVersion = { version_id: 'v1', artifact_id: 'a1', name: 'Research report', state: 'ready', size_bytes: 40,
  created_at: '2026-10-13T10:00:00Z', retention: { retained: true, owner: 'user', reason: 'explicit publication' },
  provenance: { sandbox_name: 'Research sandbox', selected_paths: ['report.txt'] },
  manifest: [{ path: 'report.txt', directory: false, size: 40 }] };
beforeEach(() => {
  vi.restoreAllMocks(); useLocale.setState({ locale: 'en' });
  useWorldStore.setState({ cards: [card], events: [], socketState: 'closed' });
});
afterEach(cleanup);

it('opens a visible loading dialog immediately, restores focus, and ignores a late response after closing', async () => {
  let finish!: (value: unknown) => void;
  const api = vi.spyOn(worldApi, 'artifacts').mockImplementation((_, path) => path?.includes('/preview')
    ? new Promise(resolve => { finish = resolve; }) : Promise.resolve([version]) as never);
  render(<ArtifactCollection card={card} />);
  const button = await screen.findByRole('button', { name: 'Preview' });
  button.focus(); fireEvent.click(button);
  expect(within(screen.getByRole('dialog')).getByRole('status').textContent).toBe('Loading preview…');
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).toBeNull(); expect(document.activeElement).toBe(button);
  await act(async () => finish({ state: 'text', text: 'late result' }));
  expect(screen.queryByText('late result')).toBeNull();
  expect(api).toHaveBeenCalledTimes(2);
});

it('keeps preview errors and retry in the dialog, handles binary files, and does not refresh the collection', async () => {
  const api = vi.spyOn(worldApi, 'artifacts').mockResolvedValueOnce([version])
    .mockRejectedValueOnce(new Error('Preview unavailable')).mockResolvedValueOnce({ state: 'binary' });
  render(<ArtifactCollection card={card} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
  expect((await screen.findByRole('alert')).textContent).toContain('Preview unavailable');
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
  expect(await screen.findByText('This file has no text preview. Download it to view its contents.')).toBeTruthy();
  expect(within(screen.getByRole('dialog')).getByRole('link', { name: 'Download' })).toBeTruthy();
  expect(api).toHaveBeenCalledTimes(3);
});

it('renders preview content as inert text and marks truncated previews', async () => {
  vi.spyOn(worldApi, 'artifacts').mockResolvedValueOnce([version]).mockResolvedValueOnce({ state: 'text', text: '<script>alert(1)</script>', truncated: true });
  render(<ArtifactCollection card={card} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Preview' }));
  expect(await screen.findByText('<script>alert(1)</script>')).toBeTruthy();
  expect(screen.getByText('Preview limited to 64 KiB. Download for the complete file.')).toBeTruthy();
  expect(screen.getByRole('dialog').querySelector('script')).toBeNull();
});

it('copies the selected version and keeps copy errors next to the destination fields', async () => {
  const sandbox = { id: 'sandbox', ...buildCardDraft('sandbox', { x: 0, y: 0 }) };
  useWorldStore.setState({ cards: [card, sandbox] });
  const api = vi.spyOn(worldApi, 'artifacts').mockResolvedValueOnce([version]).mockRejectedValueOnce(new Error('Directory already exists'));
  render(<ArtifactCollection card={card} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Copy into Sandbox' }));
  const dialog = within(screen.getByRole('dialog'));
  fireEvent.change(dialog.getByLabelText('Copy into Sandbox'), { target: { value: sandbox.id } });
  fireEvent.change(dialog.getByLabelText('New destination directory'), { target: { value: 'existing' } });
  fireEvent.click(dialog.getByRole('button', { name: 'Copy version to workspace' }));
  expect((await dialog.findByRole('alert')).textContent).toContain('Directory already exists');
  expect(api).toHaveBeenLastCalledWith('outputs', 'versions/v1/materialize', { sandbox_id: 'sandbox', destination: 'existing' });
});
