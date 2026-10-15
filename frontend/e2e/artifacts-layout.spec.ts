import { expect, test } from '@playwright/test';

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } });
});

test('artifact hierarchy and immediate preview in standalone and composed workspaces', async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const collection = await (await request.post('/api/nodes', { data: { type: 'core.artifact-collection', name: 'Research artifacts', position: { x: 300, y: 300 } } })).json();
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en', 'oaw-theme': 'light',
    'oaw-node-surfaces-v1': JSON.stringify({ state: { surfaceLevels: { [collection.id]: 'workspace' } }, version: 2 }),
  } } });
  let populated = true;
  const names = ['Transport study — final report', 'Relaxed structures and validation results', 'Remote job diagnostics'];
  const versions = Array.from({ length: 9 }, (_, i) => ({ version_id: `v${i}`, artifact_id: `a${i}`, name: names[i % 3], state: i === 2 ? 'failed' : 'ready',
    error: i === 2 ? 'Source files changed during publication. Finalize the files and publish again.' : undefined,
    size_bytes: 15678, created_at: `2026-10-13T${String(19 - i).padStart(2, '0')}:35:00Z`, retention: { retained: true, owner: 'user', reason: 'explicit publication' },
    provenance: { agent_name: 'MatCreator', sandbox_name: 'Research files & compute', run_id: 'run-123', selected_paths: ['results'] },
    manifest: Array.from({ length: i === 1 ? 5 : 1 }, (_, n) => ({ path: n ? `results/structures/relaxed-structure-${n}.cif` : 'results/report.md', size: 2115, directory: false, sha256: 'ab'.repeat(32) })),
  }));
  await page.route(`**/api/artifact-collections/${collection.id}/versions`, route => route.fulfill({ json: populated ? versions : [] }));
  await page.route(`**/api/artifact-collections/${collection.id}/versions/*/preview?*`, async route => {
    await new Promise(resolve => setTimeout(resolve, 500));
    await route.fulfill({ json: { state: 'text', text: '# Transport study\n\nValidated output for the selected research step.\n\nConductivity: 1.2 mS/cm\nTemperature: 300 K\n\nReview the assumptions before drawing a scientific conclusion.' } });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  // Use the application's surface action; the collection itself and modal are production components.
  await page.evaluate(async id => {
    // @ts-expect-error Vite serves this browser-only module path in the development fixture.
    const { useNodeSurfaceStore } = await import('/src/state/nodeSurfaces.ts' /* @vite-ignore */);
    useNodeSurfaceStore.getState().openWorkspace(id);
  }, collection.id);
  let app = page.getByRole('dialog', { name: 'Research artifacts workspace', exact: true });
  await expect(app.locator('.artifact-entry')).toHaveCount(9);
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await expect.poll(async () => (await app.boundingBox())?.x ?? -1).toBeGreaterThan(0);
  await expect(app.getByRole('heading', { name: names[0], exact: true }).first()).toBeInViewport();
  await page.screenshot({ path: '../.outputs/artifacts/standalone-light.png' });
  await app.getByRole('button', { name: 'Preview', exact: true }).first().click();
  const preview = page.getByRole('dialog', { name: 'results/report.md', exact: true });
  await expect(preview.getByRole('status')).toHaveText('Loading preview…');
  await expect(preview.locator('.artifact-preview')).toContainText('Conductivity');
  await page.screenshot({ path: '../.outputs/artifacts/preview-light.png' });
  await page.keyboard.press('Escape');
  await expect(preview).toHaveCount(0);
  await expect(app.getByRole('button', { name: 'Preview', exact: true }).first()).toBeFocused();
  await app.getByRole('button', { name: 'Show all 5 files' }).click();
  await expect(app.locator('.artifact-entry').nth(1).locator('.artifact-files > li')).toHaveCount(5);
  await page.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
  await page.screenshot({ path: '../.outputs/artifacts/standalone-dark.png' });
  await app.getByRole('button', { name: 'Close workspace', exact: true }).click();

  const notes = await (await request.post('/api/nodes', { data: { type: 'text', name: 'Research notes', position: { x: 700, y: 300 } } })).json();
  const groupResponse = await request.post('/api/legion-groups', { data: { name: 'Research studio', node_ids: [collection.id, notes.id] } });
  expect(groupResponse.ok(), await groupResponse.text()).toBe(true);
  const group = (await groupResponse.json())[0];
  await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: { version: 2, hidden_sections: [], root: {
    kind: 'split', axis: 'horizontal', ratio: .35, first: { kind: 'pane', view: { card_id: collection.id } }, second: { kind: 'pane', view: { card_id: notes.id } },
  } } } } });
  await page.reload();
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.locator(`[data-card-id="${group.id}"]`).getByRole('button', { name: 'Workspace mode', exact: true }).evaluate((button: HTMLButtonElement) => button.click());
  app = page.getByRole('dialog', { name: 'Research studio workspace mode', exact: true });
  await expect(app.locator('.artifact-entry')).toHaveCount(9);
  for (const theme of ['light', 'dark']) {
    await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
    await page.setViewportSize({ width: 1000, height: 800 });
    const fit = await app.locator('.artifact-collection').evaluate(el => el.scrollWidth <= el.clientWidth + 1);
    expect(fit).toBe(true);
    await page.screenshot({ path: `../.outputs/artifacts/composed-narrow-${theme}.png` });
    await app.getByRole('button', { name: 'Preview', exact: true }).first().click();
    await expect(preview.locator('.artifact-preview')).toBeVisible();
    await page.screenshot({ path: `../.outputs/artifacts/composed-preview-${theme}.png` });
    await preview.getByRole('button', { name: 'Close', exact: true }).click();
  }
  populated = false;
  await app.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(app.getByText('No published files yet', { exact: true })).toBeVisible();
  await page.screenshot({ path: '../.outputs/artifacts/empty-dark.png' });
  expect(errors).toEqual([]);
});
