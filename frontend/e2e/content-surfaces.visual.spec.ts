import { expect, test, type Locator, type Page } from '@playwright/test';

const phase = process.env.OAW_SURFACE_CAPTURE_PHASE ?? 'after';
const output = `../.outputs/content-surfaces/${phase}`;
const fixtureIds = new Set<string>();

test.afterEach(async ({ playwright }, testInfo) => {
  const cleanup = await playwright.request.newContext({ baseURL: testInfo.project.use.baseURL });
  try {
    const world = await (await cleanup.get('/api/world')).json();
    const ids = world.nodes.filter((node: { id: string }) => fixtureIds.has(node.id)).map((node: { id: string }) => node.id);
    if (ids.length) expect((await cleanup.post('/api/nodes/batch-delete', { data: { node_ids: ids } })).ok()).toBe(true);
  } finally { fixtureIds.clear(); await cleanup.dispose(); }
});

// Capture the production card at a known container size; no content styles are replaced.
async function capture(page: Page, card: Locator, name: string) {
  await page.setViewportSize({ width: 1400, height: 1000 });
  const originalStyle = await card.getAttribute('style');
  const cameraStyle = await page.addStyleTag({ content: '/* Capture camera only. */' });
  try {
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      for (const width of [360, 960]) {
        await card.evaluate((element, size) => { const host = element as HTMLElement; host.style.width = `${size}px`; host.style.height = '620px'; }, width);
        await expect.poll(async () => {
          const camera = await card.evaluate(element => { const matrix = new DOMMatrix(getComputedStyle(element.closest('.react-flow__node')!).transform); return { x: 30 - matrix.m41, y: 30 - matrix.m42 }; });
          await cameraStyle.evaluate((element, { x, y }) => { element.textContent = `.react-flow__viewport{transform:translate(${x}px,${y}px) scale(1)!important;}`; }, camera);
          return card.evaluate(element => new Promise<number>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => { const bounds = element.getBoundingClientRect(); resolve(Math.max(Math.abs(bounds.x - 30), Math.abs(bounds.y - 30))); }))));
        }).toBeLessThan(2);
        // Clear selection through the real canvas so resize handles retain no
        // stale world-space bounds while only the content container is resized.
        await page.locator('.react-flow__pane').click({ position: { x: 8, y: 100 } });
        if (phase === 'after') {
          expect(await card.locator('.node-surface-header, .node-inspector-footer, .workspace-titlebar').count()).toBe(0);
          await expect(card.locator('.content-surface-grip')).toBeVisible();
        }
        await card.screenshot({ path: `${output}/${name}-${theme}-${width}.png` });
      }
    }
  } finally {
    await card.evaluate((element, value) => { if (value === null) element.removeAttribute('style'); else element.setAttribute('style', value); }, originalStyle);
    await cameraStyle.evaluate(element => element.remove());
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  }
}

for (const type of ['oaw.tasks', 'matcreator.tasks', 'science.structure-viewer']) {
  test(`${type} content surface preserves drag and actions`, async ({ page, request }) => {
    test.setTimeout(90_000);
    const profile = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-node-surfaces-v1': null, 'oaw-canvas-viewport-v1': null,
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw-progressive-tutorials-v1': JSON.stringify({ version: 1, state: { enabled: false, progress: {} } }),
    } } });
    const ids: string[] = [];
    const create = async (kind: string, name: string, x = 100) => {
      const response = await request.post('/api/nodes', { data: { type: kind, name, position: { x, y: 200 } } });
      expect(response.status()).toBe(201); const node = await response.json(); ids.push(node.id); fixtureIds.add(node.id); return node;
    };
    const node = await create(type, 'Materials work');
    let sourceId: string | undefined;
    try {
      if (type === 'science.structure-viewer') {
        const room = await create('conversation', 'Structure source', 1600);
        sourceId = room.id;
        const summary = await (await request.get(`/api/conversations/${room.id}`)).json();
        const base = `/api/conversations/${room.id}/sessions/${summary.sessions[0].id}`;
        const upload = await request.post(`${base}/attachments?filename=water.xyz`, { data: Buffer.from('3\nWater\nO 0 0 0\nH 0.96 0 0\nH -0.24 0.93 0\n'), headers: { 'Content-Type': 'application/octet-stream' } });
        expect(upload.status()).toBe(201); const file = await upload.json();
        await request.post(`${base}/messages`, { data: { attachments: [{ version_id: file.version_id, path: file.path }] } });
        expect((await request.post('/api/edges', { data: { source: node.id, target: room.id, relationship: 'core.file-preview' } })).status()).toBe(201);
      }
      await page.setViewportSize({ width: 1600, height: 1000 });
      await page.goto('/');
      const card = page.locator(`[data-card-id="${node.id}"]`);
      await page.getByRole('button', { name: 'Fit view', exact: true }).click();
      await card.locator('.card-kind-icon').click();
      await expect(card).toHaveAttribute('data-surface-level', 'inspector');
      if (phase === 'after') {
        await page.getByRole('button', { name: 'Fit view', exact: true }).click();
        const before = await (await request.get(`/api/nodes/${node.id}`)).json();
        const handle = card.locator('.content-surface-grip');
        await expect(handle).toBeInViewport();
        await expect.poll(() => handle.evaluate(element => new Promise<number>(resolve => {
          const first = element.getBoundingClientRect(); let frames = 0; let movement = 0;
          const sample = () => {
            const next = element.getBoundingClientRect();
            movement = Math.max(movement, ...(['x', 'y', 'width', 'height'] as const).map(key => Math.abs(first[key] - next[key])));
            if (++frames === 5) resolve(movement); else requestAnimationFrame(sample);
          }; requestAnimationFrame(sample);
        }))).toBeLessThan(.1);
        const bounds = (await handle.boundingBox())!;
        await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + Math.min(60, bounds.height / 2));
        await page.mouse.down(); await page.mouse.move(bounds.x + bounds.width / 2 + 80, bounds.y + Math.min(60, bounds.height / 2) + 40, { steps: 12 }); await page.mouse.up();
        await expect.poll(async () => { const next = await (await request.get(`/api/nodes/${node.id}`)).json(); return Math.hypot(next.position.x - before.position.x, next.position.y - before.position.y); }).toBeGreaterThan(20);
      }
      await capture(page, card, `${type}-empty-inspector`);
      if (type === 'oaw.tasks') {
        expect((await request.post(`/api/nodes/${node.id}/actions/upsert`, { data: { expected_revision: 0, arguments: { tasks: [
          { id: 'sources', title: 'Collect reference structures', status: 'done' },
          { id: 'compare', title: 'Compare stable candidates', depends_on: ['sources'] },
          { id: 'report', title: 'Write the research report', depends_on: ['compare'] },
        ] } } })).ok()).toBe(true);
        await expect(card.getByRole('button', { name: 'Edit task Compare stable candidates', exact: true })).toBeVisible();
      } else if (type === 'matcreator.tasks') {
        expect((await request.post(`/api/nodes/${node.id}/actions/create_plan`, { data: { expected_revision: 0, arguments: { title: 'Solid electrolyte screening', goal: 'Compare structure stability and transport evidence.', tasks: [
          { id: 'sources', title: 'Collect reference structures', status: 'done', result: 'Verified source structures and citations.' },
          { id: 'compare', title: 'Compare stable candidates', status: 'review', depends_on: ['sources'] },
          { id: 'report', title: 'Write the research report', depends_on: ['compare'] },
        ] } } })).ok()).toBe(true);
        await expect(card.getByRole('button', { name: 'Compare stable candidates', exact: true })).toBeVisible();
      } else {
        await page.getByRole('button', { name: 'Fit view', exact: true }).click();
        const source = page.locator(`[data-card-id="${sourceId}"]`);
        await source.getByRole('button', { name: 'Open water.xyz', exact: true }).evaluate((button: HTMLButtonElement) => button.click());
        const preview = page.getByRole('dialog', { name: 'Preview water.xyz', exact: true });
        await preview.getByRole('button', { name: 'Close', exact: true }).click();
        await expect(card.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '3', { timeout: 45_000 });
      }
      await capture(page, card, `${type}-inspector`);
      await card.getByRole('button', { name: 'Open workspace', exact: true }).evaluate((button: HTMLButtonElement) => button.click());
      await expect(card).toHaveAttribute('data-surface-level', 'workspace');
      await capture(page, card, `${type}-workspace`);
      if (phase === 'after') {
        await card.locator('.content-surface-menu > summary').click();
        await card.getByRole('button', { name: 'Rename', exact: true }).click();
        await card.locator('.workspace-name-input').fill('Reviewed materials');
        await card.locator('.workspace-name-input').press('Enter');
        await expect.poll(async () => (await (await request.get(`/api/nodes/${node.id}`)).json()).name).toBe('Reviewed materials');
        await card.locator('.content-surface-menu > summary').click();
        await card.getByRole('button', { name: 'Close workspace', exact: true }).click();
        await expect(card).toHaveAttribute('data-surface-level', 'inspector');
      }
    } finally { await request.post('/api/nodes/batch-delete', { data: { node_ids: ids } }); }
  });
}
