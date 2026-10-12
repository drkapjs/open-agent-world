import { expect, test } from '@playwright/test';

const phase = process.env.OAW_BUILTIN_CAPTURE_PHASE ?? 'after';
const output = `../.outputs/builtin-cards/${phase}`;

test('standalone builtin content in narrow and wide light and dark containers', async ({ page, request }) => {
  test.setTimeout(150_000);
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation, changes: {
    'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }), 'oaw.locale': 'en',
    'oaw-node-surfaces-v1': null, 'oaw-theme': 'light', 'oaw-canvas-viewport-v1': null,
  } } });
  await page.setViewportSize({ width: 1600, height: 1100 });
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  for (const [type, name, selector] of [
    ['agent', 'Research agent', '.agent-window-body'],
    ['matcreator.tasks', 'Research tasks', '[aria-label="Research task board"]'],
    ['data.sqlite', 'Research database', '.sqlite-app'],
    ['data.visualization.line', 'Thermal measurements', '.viz-app'],
    ['science.structure-viewer', 'Atomic structure', '.structure-viewer'],
    ['core.artifact-collection', 'Published artifacts', '.artifact-collection'],
  ]) {
    const response = await request.post('/api/nodes', { data: { type, name, position: { x: 300, y: 250 } } });
    expect(response.status(), await response.text()).toBe(201);
    const card = await response.json();
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    const canvas = page.locator(`[data-card-id="${card.id}"]`);
    await expect(canvas).toBeVisible();
    if (await canvas.getAttribute('data-surface-level') === 'preview') await canvas.dblclick();
    // Invoke the visible card's existing action; its enlarged window may extend
    // outside the canvas viewport before Fit view is applied again.
    const opener = canvas.getByRole('button', { name: 'Open workspace', exact: true });
    if (await opener.count()) await opener.evaluate((button: HTMLButtonElement) => button.click());
    const surface = page.locator(`[data-workspace-node-id="${card.id}"]`);
    await expect(surface).toBeVisible();
    await expect(surface.locator('.workspace-content')).not.toBeEmpty();
    await expect(surface.locator(selector)).toBeVisible();
    const tutorial = page.locator('.card-tutorial-nudge');
    if (await tutorial.count()) await tutorial.getByRole('button', { name: /Close/ }).click();
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.screenshot({ path: `${output}/${type}-canvas.png` });
    // Resize only the real content container, independently of the canvas zoom.
    // The containing node's transform is unchanged; screenshot at CSS pixel scale.
    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      for (const width of [360, 1060]) {
        const camera = await surface.evaluate(element => {
          const node = element.closest('.react-flow__node') as HTMLElement;
          const matrix = new DOMMatrix(getComputedStyle(node).transform);
          return { x: 30 - matrix.m41, y: 30 - matrix.m42 };
        });
        const cameraStyle = await page.addStyleTag({ content: `.react-flow__viewport { transform: translate(${camera.x}px, ${camera.y}px) scale(1) !important; }` });
        await surface.evaluate((element, value) => {
          const host = element as HTMLElement;
          host.style.width = `${value}px`; host.style.height = '720px';
          const card = host.closest('.world-card') as HTMLElement | null;
          if (card) { card.style.width = `${value}px`; card.style.height = '720px'; }
          const node = host.closest('.react-flow__node') as HTMLElement;
          const viewport = host.closest('.react-flow__viewport') as HTMLElement;
          // Neutralize only the canvas camera for a legible 1:1 content capture.
          // Keep the production window mounted and all its actual UI/data intact.
          if (node && viewport) {
            const matrix = new DOMMatrix(getComputedStyle(node).transform);
            viewport.style.transform = `translate(${30 - matrix.m41}px, ${30 - matrix.m42}px) scale(1)`;
          }
        }, width);
        await surface.screenshot({ path: `${output}/${type}-${theme}-${width}.png` });
        await cameraStyle.evaluate(element => element.remove());
      }
    }
    expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: [card.id] } })).ok()).toBe(true);
  }
  expect(errors).toEqual([]);
});
