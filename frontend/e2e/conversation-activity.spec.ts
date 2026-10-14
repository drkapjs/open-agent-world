import { expect, test } from '@playwright/test';

test('execution groups stream, archive previous work, and retain their position after reload', async ({ page, request }) => {
  test.setTimeout(90_000);
  expect((await request.post('/api/test/conversation-activity/install?incremental=true')).ok()).toBe(true);
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
  } } });
  const agent = await (await request.post('/api/nodes', { data: { type: 'agent', name: 'Review worker' } })).json();
  const room = await (await request.post('/api/nodes', { data: { type: 'conversation', name: 'Activity review', position: { x: 300, y: 250 } } })).json();
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
    await surface.getByLabel('Conversation message', { exact: true }).fill('Review the changes and report what you find.');
    await surface.getByRole('button', { name: 'Send message', exact: true }).click();
    const groups = surface.locator('.conversation-execution');
    await expect(groups).toHaveCount(1);
    await expect(groups.locator('.run-tool')).toHaveCount(1);
    await expect(groups.locator('[aria-label="Running"]')).toHaveCount(2);
    const snapshot = await (await request.get(`${base}/timeline`)).json();
    const runId = snapshot.active_runs[0].run_id;
    for (let index = 0; index < 7; index++) {
      await request.post(`/api/test/conversation-activity/${runId}/step/0/${index}`);
      await expect(groups.locator('.run-tool')).toHaveCount(index + 2);
    }
    await expect(surface.locator('.conversation-speaker-name')).toHaveText('Review worker');
    await expect(surface.locator('.conversation-speaker-avatar')).toHaveCount(1);
    const narration = surface.locator('.conversation-message-bubble').first();
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(1);
    expect(await narration.evaluate(node => getComputedStyle(node).borderTopWidth)).toBe('1px');
    expect(await groups.evaluate(node => Boolean(node.closest('.conversation-message-bubble')))).toBe(false);
    expect((await groups.boundingBox())!.y - (await narration.boundingBox())!.y - (await narration.boundingBox())!.height).toBeLessThanOrEqual(14);
    await expect(groups.locator('.conversation-execution-summary')).not.toContainText('Review worker');
    await expect(groups.locator('.conversation-execution-summary')).not.toContainText('Execution details');
    const stop = surface.getByRole('button', { name: 'Stop', exact: true });
    expect((await stop.boundingBox())!.width).toBeLessThan(120);
    const scroll = groups.locator('.conversation-execution-scroll');
    const size = await scroll.evaluate(node => ({ height: node.clientHeight, total: node.scrollHeight, top: node.scrollTop }));
    expect(size.height).toBeLessThanOrEqual(110);
    expect(size.total).toBeGreaterThan(size.height);
    expect(size.top + size.height).toBeGreaterThanOrEqual(size.total - 2);
    const outer = surface.locator('.workspace-transcript');
    const outerTop = await outer.evaluate(node => node.scrollTop);
    await scroll.evaluate(node => { node.scrollTop = 0; node.dispatchEvent(new Event('scroll')); });
    await expect(groups.getByRole('button', { name: 'Latest activity' })).toBeVisible();
    await request.post(`/api/test/conversation-activity/${runId}/step/0/7`);
    await expect(groups.locator('.run-tool')).toHaveCount(9);
    expect(await scroll.evaluate(node => node.scrollTop)).toBe(0);
    expect(await outer.evaluate(node => node.scrollTop)).toBe(outerTop);
    await groups.getByRole('button', { name: 'Latest activity' }).click();
    for (let index = 8; index < 11; index++) {
      await request.post(`/api/test/conversation-activity/${runId}/step/0/${index}`);
      await expect(groups.locator('.run-tool')).toHaveCount(index + 2);
    }
    const capture = async (prefix = '') => { for (const theme of ['light', 'dark']) {
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
        await expect(groups.last()).toBeVisible();
        const bounds = await groups.last().evaluate(node => ({ w: node.clientWidth, sw: node.scrollWidth }));
        expect(bounds.sw).toBeLessThanOrEqual(bounds.w + 1);
        await page.mouse.move(1400, 950);
        await surface.screenshot({ path: `../.outputs/conversation-activity/${prefix}${theme}-${width}.png` });
        await style.evaluate(node => node.remove());
      }
    } };
    await capture();
    await request.post(`/api/test/conversation-activity/${runId}/advance/0`);
    await expect(groups).toHaveCount(2);
    await expect(groups.nth(0).getByRole('button', { name: /tool calls/ })).toHaveAttribute('aria-expanded', 'false');
    for (let index = 0; index < 7; index++) {
      await request.post(`/api/test/conversation-activity/${runId}/step/1/${index}`);
      await expect(groups.nth(1).locator('.run-tool')).toHaveCount(index + 2);
    }
    await expect(groups.nth(1).locator('.run-tool')).toHaveCount(8);
    const progress = surface.getByText('The files agree. I am checking the implementation next.', { exact: true });
    await expect(progress).toBeVisible();
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(2);
    await expect(surface.locator('.conversation-speaker-avatar')).toHaveCount(1);
    await expect(surface.locator('.conversation-speaker-name')).toHaveCount(1);
    expect((await groups.nth(0).boundingBox())!.y).toBeLessThan((await progress.boundingBox())!.y);
    expect((await progress.boundingBox())!.y).toBeLessThan((await groups.nth(1).boundingBox())!.y);
    await capture('between-');
    const ids = await groups.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-execution-id')));
    await page.reload();
    await expect(groups).toHaveCount(2);
    await expect(progress).toBeVisible();
    expect(await groups.evaluateAll(nodes => nodes.map(node => node.getAttribute('data-execution-id')))).toEqual(ids);
    await request.post(`/api/test/conversation-activity/${runId}/advance/1`);
    await expect(surface.getByText('Review complete. One check needs attention.', { exact: true })).toBeVisible();
    await expect(surface.locator('.conversation-message-bubble')).toHaveCount(3);
    await expect(surface.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0);
    await expect(groups.nth(1).getByRole('button', { name: /tool calls/ })).toHaveAttribute('aria-expanded', 'false');
    await expect(groups.nth(1)).toContainText('1 failed');
    await capture('completed-');
    await page.reload();
    await expect(groups).toHaveCount(2);
    await expect(progress).toBeVisible();
    const toggle = groups.nth(0).getByRole('button', { name: /tool calls/ });
    await toggle.focus();
    await page.keyboard.press('Enter');
    await expect(groups.nth(0).locator('.run-tool')).toHaveCount(12);
    await groups.nth(0).locator('.run-tool > summary').first().click();
    await expect(groups.nth(0).getByText(/Checked module 0/)).toBeVisible();
    await surface.screenshot({ path: '../.outputs/conversation-activity/restored.png' });
    expect(errors).toEqual([]);
  } finally {
    await request.delete(`/api/nodes/${room.id}`);
    await request.delete(`/api/nodes/${agent.id}`);
    await request.post('/api/test/conversation-input/restore');
  }
});
