import { expect, test, type Locator, type Page } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const output = '../.outputs/builtin-cards/after';

async function resize(page: Page, surface: Locator, width: number) {
  const camera = await surface.evaluate((element, width) => {
    const host = element as HTMLElement;
    host.style.width = `${width}px`;
    host.style.height = '720px';
    // Keep the real card boundary aligned with the resized content window;
    // otherwise its selection outline retains the original canvas dimensions.
    const card = host.closest<HTMLElement>('.world-card');
    if (card) { card.style.width = `${width}px`; card.style.height = '720px'; }
    const node = host.closest('.react-flow__node')!;
    const matrix = new DOMMatrix(getComputedStyle(node).transform);
    return { x: 30 - matrix.m41, y: 30 - matrix.m42 };
  }, width);
  return page.addStyleTag({ content: `.react-flow__viewport { transform: translate(${camera.x}px, ${camera.y}px) scale(1) !important; }` });
}

async function expectContained(element: Locator, host: Locator) {
  await expect(element).toBeVisible();
  const rect = (await element.boundingBox())!;
  const bounds = (await host.boundingBox())!;
  expect(rect.x).toBeGreaterThanOrEqual(bounds.x - 1);
  expect(rect.y).toBeGreaterThanOrEqual(bounds.y - 1);
  expect(rect.x + rect.width).toBeLessThanOrEqual(bounds.x + bounds.width + 1);
  expect(rect.y + rect.height).toBeLessThanOrEqual(bounds.y + bounds.height + 1);
}

test('conversation and compute retain their working content across compact views and themes', async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1600, height: 1100 });
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
  } } });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const folder = path.resolve('..', '.tmp', `builtin-compute-${Date.now()}`);
  await mkdir(folder, { recursive: true });
  await writeFile(path.join(folder, 'research-notes.md'), '# Transport study\n\nCompare conductivity at 300 K.\n');
  for (const type of ['conversation', 'sandbox']) {
    const response = await request.post('/api/nodes', { data: { type, name: `${type} design review`, position: { x: 300, y: 250 },
      ...(type === 'sandbox' ? { config: { workspace_path: folder } } : {}),
    } });
    expect(response.status(), await response.text()).toBe(201);
    const card = await response.json();
    try {
      await page.goto('/');
      await page.getByRole('button', { name: 'Fit view', exact: true }).click();
      const canvas = page.locator(`[data-card-id="${card.id}"]`);
      if (await canvas.getAttribute('data-surface-level') === 'preview') await canvas.dblclick();
      const opener = canvas.getByRole('button', { name: 'Open workspace', exact: true });
      if (await opener.count()) await opener.evaluate((button: HTMLButtonElement) => button.click());
      const surface = page.locator(`[data-workspace-node-id="${card.id}"]`);
      await expect(surface).toBeVisible();
      const tutorial = page.locator('.card-tutorial-nudge');
      if (await tutorial.count()) await tutorial.getByRole('button', { name: /Close/ }).click();
      let camera = await resize(page, surface, 1060);
      if (type === 'conversation') {
        await expect(surface.getByText('This session is ready', { exact: true })).toBeVisible();
        await surface.screenshot({ path: `${output}/conversation-empty-light-1060.png` });
        const composer = surface.getByLabel('Conversation message', { exact: true });
        await composer.fill('The first observation is recorded as a note, without invoking an Agent.');
        await surface.getByRole('button', { name: 'Send message', exact: true }).click();
        await expect(surface.getByText('The first observation is recorded as a note, without invoking an Agent.', { exact: true })).toBeVisible();
        await composer.fill('Draft: compare the two temperature ranges.');
      } else {
        await expect(surface.getByText('Select a file to preview', { exact: true })).toBeVisible();
        await surface.screenshot({ path: `${output}/sandbox-empty-light-1060.png` });
        await surface.getByRole('button', { name: 'research-notes.md', exact: true }).click();
        await expect(surface.locator('.sandbox-preview pre')).toContainText('Transport study');
      }
      await camera.evaluate(element => element.remove());
      for (const theme of ['light', 'dark']) {
        await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
        for (const width of [360, 1060]) {
          camera = await resize(page, surface, width);
          if (type === 'conversation') {
            const composer = surface.getByLabel('Conversation message', { exact: true });
            if (width === 360) {
              const navigation = surface.getByRole('navigation', { name: 'Conversation views', exact: true });
              await navigation.getByRole('button', { name: 'Participants', exact: true }).click();
              await expect(composer).toBeHidden();
              await navigation.getByRole('button', { name: 'Sessions', exact: true }).click();
              await expect(surface.getByRole('button', { name: 'New session', exact: true })).toBeVisible();
              await navigation.getByRole('button', { name: 'Conversation', exact: true }).click();
            }
            await expect(composer).toHaveValue('Draft: compare the two temperature ranges.');
            await expectContained(composer, surface);
            await expectContained(surface.getByRole('button', { name: 'Send message', exact: true }), surface);
          } else if (width === 360) {
            const navigation = surface.getByRole('navigation', { name: 'Workspace views', exact: true });
            await navigation.getByRole('button', { name: 'Terminal', exact: true }).click();
            await expectContained(surface.getByLabel('Command', { exact: true }), surface);
            await surface.screenshot({ path: `${output}/sandbox-terminal-${theme}-360.png` });
            await navigation.getByRole('button', { name: 'Files', exact: true }).click();
            await surface.getByRole('button', { name: 'research-notes.md', exact: true }).click();
            await expect(surface.locator('.sandbox-preview pre')).toContainText('Transport study');
          }
          const fit = await surface.locator('.workspace-content').evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
          expect(fit.scroll).toBeLessThanOrEqual(fit.width + 1);
          await surface.screenshot({ path: `${output}/${type}-${theme}-${width}.png` });
          await camera.evaluate(element => element.remove());
        }
      }
    } finally {
      await request.post('/api/nodes/batch-delete', { data: { node_ids: [card.id] } });
    }
  }
  expect(errors).toEqual([]);
});
