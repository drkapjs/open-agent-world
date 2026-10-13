import { expect, test } from '@playwright/test';

test('followup stays after the running reply and is consumed in the same run', async ({ page, request }) => {
  test.setTimeout(90_000);
  await request.post('/api/test/conversation-input/install');
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
  } } });
  const agent = await (await request.post('/api/nodes', { data: { type: 'agent', name: 'Timeline worker' } })).json();
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Timeline', position: { x: 300, y: 250 } } })).json();
  await request.post('/api/edges', { data: { source: agent.id, target: room.id, relationship: 'participate' } });
  const session = (await (await request.get(`/api/conversations/${room.id}`)).json()).sessions[0];
  const base = `/api/conversations/${room.id}/sessions/${session.id}`;
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    const card = page.locator(`[data-card-id="${room.id}"]`);
    const opener = card.getByRole('button', { name: 'Open workspace', exact: true });
    if (await opener.count()) await opener.evaluate((node: HTMLButtonElement) => node.click());
    const surface = page.locator(`[data-workspace-node-id="${room.id}"]`);
    await expect(surface).toBeVisible();
    const composer = surface.getByLabel('Conversation message', { exact: true });
    await composer.fill('Run a, b, c in sequence.');
    await surface.getByRole('button', { name: 'Send message', exact: true }).click();
    await expect(surface.getByText('I finished a. I am checking b now.', { exact: true })).toBeVisible();
    await expect(surface.locator('.run-tool-name')).toHaveText('b');
    await composer.fill('Skip c and summarize b.');
    await surface.getByRole('button', { name: 'Send message', exact: true }).click();
    const expected = ['Run a, b, c in sequence.', 'I finished a. I am checking b now.', 'Skip c and summarize b.'];
    const messages = surface.locator('[data-message-id]');
    for (let i = 0; i < expected.length; i++) await expect(messages.nth(i)).toContainText(expected[i]);
    await expect(messages).toHaveCount(3);
    // Real browser geometry: b began before the follow-up, even though it is
    // still running, and therefore stays above it.
    expect((await surface.locator('.run-tool').boundingBox())!.y).toBeLessThan((await messages.nth(2).boundingBox())!.y);
    const snapshot = await (await request.get(`${base}/timeline`)).json();
    expect(snapshot.active_runs).toHaveLength(1);
    const runId = snapshot.active_runs[0].run_id;
    for (const theme of ['light', 'dark']) {
      await page.evaluate(theme => { document.documentElement.dataset.theme = theme; }, theme);
      for (const width of [1060, 360]) {
        const camera = await surface.evaluate((element, width) => {
          const host = element as HTMLElement;
          host.style.width = `${width}px`; host.style.height = '720px';
          const frame = host.closest<HTMLElement>('.world-card');
          if (frame) { frame.style.width = `${width}px`; frame.style.height = '720px'; }
          const matrix = new DOMMatrix(getComputedStyle(host.closest('.react-flow__node')!).transform);
          return { x: 30 - matrix.m41, y: 30 - matrix.m42 };
        }, width);
        const style = await page.addStyleTag({ content: `.react-flow__viewport { transform: translate(${camera.x}px, ${camera.y}px) scale(1) !important; }` });
        await expect(messages.nth(2)).toBeVisible();
        await surface.screenshot({ path: `../.outputs/conversation-input/${theme}-${width}.png` });
        await style.evaluate(node => node.remove());
      }
    }
    await page.reload();
    await expect(surface).toBeVisible();
    for (let i = 0; i < expected.length; i++) await expect(messages.nth(i)).toContainText(expected[i]);
    await request.post(`/api/test/conversation-input/${runId}/return-b`);
    await expect(messages).toHaveCount(4);
    await expect(messages.nth(3)).toContainText('I received your update with the result of b. I skipped c.');
    await expect(surface.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
    const final = await (await request.get(`${base}/timeline`)).json();
    expect(final.deliveries).toEqual([]);
    expect(final.items.filter((item: { sender_kind: string }) => item.sender_kind === 'agent').map((item: { run_id: string }) => item.run_id)).toEqual([runId, runId]);
    expect(errors).toEqual([]);
  } finally {
    await request.delete(`/api/nodes/${room.id}`);
    await request.delete(`/api/nodes/${agent.id}`);
    await request.post('/api/test/conversation-input/restore');
  }
});
