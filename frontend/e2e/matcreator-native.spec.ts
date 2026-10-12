import { expect, test } from '@playwright/test';

test.afterEach(async ({ request }) => {
  const nodes = await (await request.get('/api/nodes')).json();
  if (nodes.length) expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: nodes.map((node: { id: string }) => node.id) } })).ok()).toBe(true);
});

test('native research workspace renders plan and subagents and switches session state', async ({ page, request }) => {
  test.setTimeout(90_000);
  await page.setViewportSize({ width: 1600, height: 1000 });
  const instanceResponse = await request.post('/api/legions/presets/matcreator.native-research/instances', { data: {} });
  expect(instanceResponse.status(), await instanceResponse.text()).toBe(201);
  const instance = await instanceResponse.json();
  const ids = instance.node_ids;
  const conversation = await (await request.get(`/api/conversations/${ids.conversation}`)).json();
  const session = conversation.sessions[0].id;
  const projection = {
    phase: 'running', title: 'Copper structure verification', nodes: [
      { id: 'root', kind: 'agent', label: 'Orchestrator', status: 'running' },
      { id: 'planner', kind: 'agent', label: 'Planning', status: 'success', parent_id: 'root' },
      { id: 'execution_0', kind: 'agent', label: 'Execution', status: 'running', parent_id: 'planner' },
      { id: 'worker', kind: 'agent', label: 'Geometry subagent', status: 'running', parent_id: 'execution_0', detail: 'Verifying geometry through connected OAW tools.' },
      { id: 'review', kind: 'agent', label: 'Review subagent', status: 'needs_replanning', parent_id: 'execution_0' },
      { id: 'build', kind: 'plan', label: 'Build copper', status: 'success', result: 'Fixture: 32 atoms verified' },
    ],
  };
  const projected = await request.post(`/api/test/execution/${ids.agent}/${session}`, { data: projection });
  expect(projected.ok(), await projected.text()).toBe(true);
  const { run_id: runId } = await projected.json();
  const profile = await (await request.get('/api/application')).json();
  const preferences = await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
      'oaw-active-workspace-v1': JSON.stringify({ version: 0, state: { activeId: ids.group } }),
    },
  } });
  expect(preferences.ok()).toBe(true);
  const errors: string[] = [];
  let executionNotifications = 0;
  page.on('websocket', socket => socket.on('framereceived', event => {
    if (String(event.payload).includes('execution_updated')) executionNotifications++;
  }));
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  await page.goto('/');
  const workspace = page.getByRole('dialog', { name: 'MatCreator native research workspace mode' });
  await expect(workspace).toBeVisible();
  await expect(workspace.getByText('Geometry subagent')).toBeVisible();
  await expect(workspace.getByRole('textbox', { name: 'Conversation message', exact: true })).toBeVisible();
  const graph = workspace.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph.locator('[data-graph-edge]')).toHaveCount(4);
  await expect(graph.getByText('Copper structure verification')).toBeHidden();
  await graph.locator('summary').click();
  await expect(graph.getByText('Copper structure verification')).toBeVisible();
  await graph.locator('summary').click();
  const viewport = graph.getByLabel('Execution graph canvas', { exact: true });
  const canvas = graph.locator('.execution-graph-canvas');
  const assertFit = async () => {
    const bounds = await viewport.boundingBox();
    for (const node of await graph.locator('.execution-graph-node').all()) {
      const box = await node.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(bounds!.x);
      expect(box!.x + box!.width).toBeLessThanOrEqual(bounds!.x + bounds!.width);
      expect(box!.y).toBeGreaterThanOrEqual(bounds!.y);
      expect(box!.y + box!.height).toBeLessThanOrEqual(bounds!.y + bounds!.height);
    }
  };
  await assertFit();
  const center = await workspace.getByRole('textbox', { name: 'Conversation message', exact: true }).boundingBox();
  const left = await graph.boundingBox();
  const right = await workspace.locator('[data-workspace-section-pane="sessions"]').boundingBox();
  expect(left!.x + left!.width).toBeLessThan(center!.x);
  expect(right!.x).toBeGreaterThan(center!.x + center!.width);
  await page.screenshot({ path: 'test-results/matcreator-native-workspace.png' });
  await graph.getByRole('button', { name: 'Plan', exact: true }).click();
  await graph.getByRole('button', { name: 'Build copper: success' }).click();
  await expect(graph.getByText('Fixture: 32 atoms verified')).toBeVisible();
  await graph.getByRole('button', { name: 'Agents', exact: true }).click();
  await graph.getByRole('button', { name: 'Geometry subagent: running' }).click();
  const beforePan = await canvas.getAttribute('style');
  const bounds = (await viewport.boundingBox())!;
  await page.mouse.move(bounds.x + 15, bounds.y + 30);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 55, bounds.y + 60, { steps: 6 });
  await page.mouse.up();
  await expect(canvas).not.toHaveAttribute('style', beforePan!);
  await expect(graph.getByRole('region', { name: 'Execution details' })).toBeVisible();
  const beforeZoom = Number(await canvas.getAttribute('data-scale'));
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.wheel(0, -180);
  await expect.poll(async () => Number(await canvas.getAttribute('data-scale'))).toBeGreaterThan(beforeZoom);
  const manualCamera = await canvas.getAttribute('style');
  // Freeze polling after a completed read. A WebSocket invalidation must update
  // the same Run with only 100 ms of browser timer time, without blanking details.
  await page.waitForResponse(response => response.url().includes(`/execution-sources/${ids.agent}`));
  await page.clock.pauseAt(new Date(Date.now() + 100));
  const notificationsBefore = executionNotifications;
  const updated = await request.post(`/api/test/execution/${ids.agent}/${session}`, { data: {
    ...projection, run_id: runId, phase: 'succeeded',
    nodes: projection.nodes.map(node => ({ ...node, status: 'success', result: 'Live result verified' })),
  } });
  expect(updated.ok(), await updated.text()).toBe(true);
  await expect.poll(() => executionNotifications).toBeGreaterThan(notificationsBefore);
  await page.clock.runFor(100);
  await expect(graph.getByRole('button', { name: 'Geometry subagent: success' })).toHaveAttribute('aria-pressed', 'true');
  await expect(graph.getByText('Live result verified')).toBeVisible();
  await expect(graph.locator('[data-phase]')).toHaveAttribute('data-phase', 'succeeded');
  await expect(canvas).toHaveAttribute('style', manualCamera!);
  await page.clock.resume();
  // A follow-up turn first has Run metadata but no native projection. Keep the
  // detailed graph, then replace it when the new worker publishes its state.
  const starting = await request.post(`/api/test/execution/${ids.agent}/${session}/start`);
  expect(starting.ok(), await starting.text()).toBe(true);
  const nextRun = (await starting.json()).run_id;
  await expect(graph.getByText('Waiting for this run’s graph…')).toBeVisible();
  await expect(graph.locator('.execution-graph-node')).toHaveCount(5);
  await expect(graph.getByRole('button', { name: 'Geometry subagent: success' })).toHaveAttribute('aria-pressed', 'true');
  await graph.screenshot({ path: 'test-results/execution-graph-starting.png' });
  const resumed = await request.post(`/api/test/execution/${ids.agent}/${session}`, { data: {
    ...projection, run_id: nextRun,
  } });
  expect(resumed.ok(), await resumed.text()).toBe(true);
  await expect(graph.getByText('Waiting for this run’s graph…')).toHaveCount(0);
  await expect(graph.getByRole('button', { name: 'Geometry subagent: running' })).toBeVisible();
  await expect(graph.getByRole('region', { name: 'Execution details' })).toHaveCount(0);
  await viewport.focus();
  await page.keyboard.press('Home');
  await assertFit();
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    await graph.screenshot({ path: `test-results/execution-graph-${theme}.png` });
  }
  await page.evaluate(() => { document.documentElement.dataset.theme = 'light'; });
  // Keep an unsent conversation draft while operations reveal other tabs.
  const composer = workspace.getByRole('textbox', { name: 'Conversation message', exact: true });
  await composer.fill('Retained research question');
  const sessionBase = `/api/conversations/${ids.conversation}/sessions/${session}`;
  const uploaded = await request.post(`${sessionBase}/attachments?filename=helium.xyz`, {
    data: Buffer.from('1\nHelium\nHe 0 0 0\n'), headers: { 'Content-Type': 'application/octet-stream' },
  });
  expect(uploaded.status()).toBe(201);
  const attachment = await uploaded.json();
  expect((await request.post(`${sessionBase}/messages`, { data: {
    attachments: [{ version_id: attachment.version_id, path: attachment.path }],
  } })).status()).toBe(202);
  const layoutBefore = (await (await request.get(`/api/nodes/${ids.group}`)).json()).config.workspace_layout;
  await workspace.getByRole('button', { name: 'Open helium.xyz', exact: true }).click();
  await expect(workspace.getByRole('tab', { name: 'Structure viewer', exact: true })).toHaveAttribute('aria-selected', 'true');
  const viewer = workspace.locator('.structure-viewer--workspace');
  await expect(viewer.locator('.structure-canvas')).toHaveAttribute('data-atom-count', '1', { timeout: 30_000 });
  await expect(viewer.locator('canvas').first()).toBeVisible();
  await expect(viewer.locator('.structure-empty[role="alert"]')).toHaveCount(0);
  await expect(page.getByRole('dialog', { name: 'Preview helium.xyz', exact: true })).toHaveCount(0);
  expect((await (await request.get(`/api/nodes/${ids.group}`)).json()).config.workspace_layout).toEqual(layoutBefore);
  await page.screenshot({ path: 'test-results/matcreator-native-structure.png' });
  await workspace.getByRole('tab', { name: 'Skills & knowledge', exact: true }).click();
  await expect(workspace.getByRole('region', { name: 'Know-Do Graph workspace' })).toBeVisible();
  await workspace.getByRole('tab', { name: /Research files & compute · File preview/ }).click();
  await expect(workspace.getByText('Select a file to preview')).toBeVisible();
  await workspace.getByRole('tab', { name: /Research conversation · Conversation/ }).click();
  await expect(composer).toHaveValue('Retained research question');

  await workspace.getByRole('button', { name: 'New session', exact: true }).click();
  await expect(workspace.getByText('No execution yet')).toBeVisible();
  await graph.screenshot({ path: 'test-results/execution-graph-empty.png' });
  await expect(workspace.getByText('Copper structure verification')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('standalone execution graph gestures stay local inside a scaled canvas', async ({ page, request }) => {
  test.setTimeout(60_000);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 1500, height: 950 });
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
      'oaw-active-workspace-v1': null, 'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
    },
  } })).ok()).toBe(true);
  const create = async (type: string, name: string, x: number) => {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x, y: 250 } } });
    expect(response.status(), await response.text()).toBe(201);
    return response.json();
  };
  const source = await create('agent', 'Researcher', 1100);
  const viewer = await create('core.execution-graph', 'Agent graph', 300);
  expect((await request.post('/api/edges', { data: { source: viewer.id, target: source.id, relationship: 'core.execution-source' } })).status()).toBe(201);
  expect((await request.post(`/api/test/execution/${source.id}/default`, { data: {
    title: 'Inspect the geometry, then review the calculation and prepare the result.', phase: 'running', nodes: [
      { id: 'root', kind: 'agent', label: 'Coordinator', status: 'running' },
      { id: 'geometry', kind: 'agent', label: 'Geometry', status: 'success', parent_id: 'root' },
      { id: 'review', kind: 'agent', label: 'Review', status: 'running', parent_id: 'root' },
    ],
  } })).ok()).toBe(true);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const card = page.locator(`[data-card-id="${viewer.id}"]`);
  await expect(card).toBeVisible();
  if (await card.getAttribute('data-surface-level') === 'preview') await card.dblclick();
  const opener = card.getByRole('button', { name: 'Open workspace', exact: true });
  if (await opener.count()) await opener.evaluate((button: HTMLButtonElement) => button.click());
  const surface = page.locator(`[data-workspace-node-id="${viewer.id}"]`);
  const graph = surface.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph.getByRole('button', { name: 'Coordinator: running' })).toBeVisible();
  const tutorial = page.locator('.card-tutorial-nudge');
  if (await tutorial.count()) await tutorial.getByRole('button', { name: /Close/ }).click();
  const canvas = graph.locator('.execution-graph-canvas');
  const viewport = graph.getByLabel('Execution graph canvas', { exact: true });
  const outer = page.locator('.react-flow__viewport');
  // Opening a workspace expands its window beyond the old card bounds. Place
  // that real window onscreen at a non-unit outer zoom before testing gestures.
  await expect.poll(async () => {
    const previous = await surface.boundingBox();
    await new Promise(resolve => setTimeout(resolve, 100));
    return JSON.stringify(previous) === JSON.stringify(await surface.boundingBox());
  }).toBe(true);
  const placeWindow = async (scale: number) => {
    const origin = await surface.evaluate((element, scale) => {
      const matrix = new DOMMatrix(getComputedStyle(element.closest('.react-flow__viewport')!).transform);
      const bounds = element.getBoundingClientRect();
      return { x: 30 - (bounds.x - matrix.m41) / matrix.a * scale, y: 30 - (bounds.y - matrix.m42) / matrix.d * scale };
    }, scale);
    return page.addStyleTag({ content: `.react-flow__viewport { transform: translate(${origin.x}px, ${origin.y}px) scale(${scale}) !important; }` });
  };
  const gestureCamera = await placeWindow(.75);
  const outerTransform = await outer.getAttribute('style');
  const before = await graph.getByRole('button', { name: 'Coordinator: running' }).boundingBox();
  const box = (await viewport.boundingBox())!;
  await page.mouse.move(box.x + 10, box.y + 15);
  await page.mouse.down();
  await page.mouse.move(box.x + 70, box.y + 45, { steps: 8 });
  await page.mouse.up();
  const after = await graph.getByRole('button', { name: 'Coordinator: running' }).boundingBox();
  expect(after!.x - before!.x).toBeCloseTo(60, 0);
  expect(after!.y - before!.y).toBeCloseTo(30, 0);
  await expect(outer).toHaveAttribute('style', outerTransform!);
  await expect(graph.getByRole('region', { name: 'Execution details' })).toHaveCount(0);
  const scale = Number(await canvas.getAttribute('data-scale'));
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -200);
  await expect.poll(async () => Number(await canvas.getAttribute('data-scale'))).toBeGreaterThan(scale);
  await expect(outer).toHaveAttribute('style', outerTransform!);
  const persisted = await (await request.get(`/api/nodes/${viewer.id}`)).json();
  expect(persisted.position).toEqual(viewer.position);
  await graph.getByRole('button', { name: 'Fit graph' }).click();
  await graph.getByRole('button', { name: 'Coordinator: running' }).click();
  await expect(graph.getByRole('region', { name: 'Execution details' })).toBeVisible();
  await graph.getByRole('button', { name: 'Close details' }).click();
  await gestureCamera.evaluate(element => element.remove());
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    for (const width of [300, 760]) {
      // Inspect the real card at 1:1, without changing saved window geometry.
      await surface.evaluate((element, width) => {
        for (const host of [element, element.closest('.world-card')]) {
          if (host instanceof HTMLElement) { host.style.width = `${width}px`; host.style.height = '700px'; }
        }
      }, width);
      const style = await placeWindow(1);
      await graph.getByRole('button', { name: 'Fit graph' }).click();
      await graph.screenshot({ path: `test-results/execution-graph-standalone-${theme}-${width}.png` });
      await style.evaluate(element => element.remove());
    }
  }
  expect(errors).toEqual([]);
});
