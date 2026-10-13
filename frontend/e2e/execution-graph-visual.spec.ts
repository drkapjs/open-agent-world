import { expect, test } from '@playwright/test';
import { graphFixture } from './fixtures/executionGraph';
import { writeFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

for (const scenario of ['independent', 'sparse', 'parallel', 'large']) {
  test(scenario + ': 300, 500 and 1000px in both themes', async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    for (const theme of ['light', 'dark']) for (const width of [300, 500, 1000]) {
      await page.goto('/dev/execution-graph.html?scenario=' + scenario + '&theme=' + theme + '&width=' + width);
      const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
      const viewport = graph.getByLabel('Execution history', { exact: true });
      await expect(graph.locator('.execution-vertex')).not.toHaveCount(0);
      if (scenario === 'large' || scenario === 'sparse') await viewport.evaluate(element => { element.scrollTop = element.scrollHeight; });
      await expect.poll(() => viewport.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      await expect(graph.locator('.execution-tracks')).toHaveCount(1);
      await expect(graph.locator('.execution-expand, .execution-fold, .execution-selection-actions, .execution-lens-viewport')).toHaveCount(0);
      if (scenario === 'independent') {
        await expect(graph.locator('[data-graph-edge]')).toHaveCount(0);
        await expect(graph.locator('path[data-kind="history"]')).toHaveCount(2);
      }
      if (scenario === 'sparse') await expect(graph.locator('[data-graph-edge]')).toHaveCount(3);
      if (scenario === 'parallel') await expect(graph.locator('[data-graph-edge]')).toHaveCount(8);
      const metrics = await graph.evaluate(element => ({ vertices: element.querySelectorAll('.execution-vertex').length,
        lanes: Array.from(element.querySelectorAll('.execution-vertex-dot')).map(dot => Number(dot.getAttribute('data-lane'))) }));
      expect(metrics.vertices).toBeLessThan(25);
      expect(Math.max(...metrics.lanes)).toBeLessThan(4);
      await page.mouse.move(0, 0);
      await graph.screenshot({ path: '../.outputs/project-history/' + scenario + '-' + theme + '-' + width + '.png', animations: 'disabled' });
    }
    expect(errors).toEqual([]);
  });
}

test('one-click details, distant dependency navigation, search and stable live history', async ({ page }) => {
  await page.goto('/dev/execution-graph.html?scenario=sparse&width=500');
  const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
  const viewport = graph.getByLabel('Execution history', { exact: true });
  await expect(graph.getByText('20 operations')).toBeVisible();
  await viewport.evaluate(element => { element.scrollTop = element.scrollHeight; });
  const last = graph.getByRole('button', { name: 'E20: running', exact: true });
  await last.locator('.execution-vertex-dot').click();
  await expect(last).toHaveAttribute('aria-expanded', 'true');
  await expect(graph.locator('[data-highlighted="true"]')).toHaveCount(3);
  await graph.screenshot({ path: '../.outputs/project-history/sparse-selected-500.png', animations: 'disabled' });
  await last.locator('strong').click();
  await expect(graph.getByLabel('Operation details')).toHaveCount(0);
  await last.click();
  await graph.getByRole('button', { name: 'Uses result E01', exact: true }).click();
  await expect(graph.getByRole('button', { name: 'E01: success', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await expect(graph.getByText('20 operations')).toBeVisible(); // navigation does not hide the rest
  await graph.getByRole('button', { name: 'Used by E20', exact: true }).click();
  await expect(last).toHaveAttribute('aria-expanded', 'true');
  await graph.getByRole('textbox', { name: 'Find operation' }).fill('E09');
  await page.keyboard.press('Enter');
  await expect(graph.getByRole('button', { name: 'E09: success' })).toHaveAttribute('aria-expanded', 'true');
  const before = await viewport.evaluate(element => ({ top: element.scrollTop,
    lanes: Array.from(element.querySelectorAll<HTMLElement>('.execution-vertex')).map(row => [row.dataset.rowId, row.querySelector('.execution-vertex-dot')?.getAttribute('data-lane')]) }));
  const value = graphFixture('sparse'); value.revision = 2;
  value.nodes.at(-1)!.status = 'failed';
  value.nodes.push({ id: 'retry', label: 'Retry E20', kind: 'agent', status: 'running', sequence: 210, retry_of: 'E20' });
  value.edges!.push({ id: 'retry-edge', source: 'E20', target: 'retry', kind: 'retry' });
  await page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
  await expect(graph.getByText('21 operations')).toBeVisible();
  await expect(graph.getByRole('button', { name: 'E09: success' })).toHaveAttribute('aria-expanded', 'true');
  expect(await viewport.evaluate(element => element.scrollTop)).toBeCloseTo(before.top, 0);
  const after = await viewport.locator('.execution-vertex').evaluateAll(rows => rows.map(row => [row.getAttribute('data-row-id'), row.querySelector('.execution-vertex-dot')?.getAttribute('data-lane')]));
  expect(after).toEqual(before.lanes);
  await graph.getByRole('button', { name: '1 new operations' }).click();
  await expect(graph.getByRole('button', { name: 'E20: failed' })).toBeVisible();
  await expect(graph.getByRole('button', { name: 'Retry E20: running' })).toHaveAttribute('aria-expanded', 'true');
  await graph.screenshot({ path: '../.outputs/project-history/retry-preserved-500.png', animations: 'disabled' });
  await viewport.focus(); await page.keyboard.press('Escape');
  await expect(graph.getByLabel('Operation details')).toHaveCount(0);
});

test('1200 operations are readable immediately with windowed scrolling and Chinese empty state', async ({ page }) => {
  await page.goto('/dev/execution-graph.html?scenario=large&width=300');
  const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
  await expect(graph.getByText('1200 operations')).toBeVisible();
  const viewport = graph.getByLabel('Execution history', { exact: true });
  const samples = await viewport.evaluate(async element => {
    const times: number[] = [];
    for (let i = 0; i < 30; i++) {
      const start = performance.now(); element.scrollTop = i / 29 * element.scrollHeight;
      await new Promise(requestAnimationFrame); await new Promise(requestAnimationFrame);
      times.push(performance.now() - start);
    }
    return { times, rows: element.querySelectorAll('.execution-vertex').length, height: element.scrollHeight };
  });
  expect(samples.height).toBeGreaterThan(1200 * 54);
  expect(samples.rows).toBeLessThan(25);
  await expect(graph.getByRole('button', { name: 'Publish result: waiting', exact: true })).toBeVisible();
  mkdirSync('../.outputs/project-history', { recursive: true });
  writeFileSync('../.outputs/project-history/scroll-metrics.json', JSON.stringify(samples, null, 2));
  await graph.screenshot({ path: '../.outputs/project-history/large-end-300.png', animations: 'disabled' });
  await page.goto('/dev/execution-graph.html?scenario=empty&width=300&locale=zh-CN&theme=dark');
  await expect(page.locator('.execution-vertex')).toHaveCount(0);
  await page.locator('main').screenshot({ path: '../.outputs/project-history/empty-dark-300.png' });
  await page.goto('/dev/execution-graph.html?scenario=parallel&width=300&locale=zh-CN&theme=dark');
  await expect(page.getByText('项目历史', { exact: true })).toBeVisible();
  await page.locator('main').screenshot({ path: '../.outputs/project-history/parallel-zh-dark-300.png' });
});

test('native worker field filtering still produces readable operations and task forks', async ({ page }) => {
  const python = path.resolve('../backend/.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const value = JSON.parse(execFileSync(python, ['e2e/fixtures/native_history.py'], { encoding: 'utf8' }));
  for (const width of [300, 500, 1000]) for (const theme of ['light', 'dark']) {
    await page.goto('/dev/execution-graph.html?scenario=empty&width=' + width + '&theme=' + theme);
    await page.evaluate(value => (window as unknown as { updateGraphFixture(value: unknown): void }).updateGraphFixture(value), value);
    const graph = page.getByRole('region', { name: 'Execution graph', exact: true });
    await expect(graph.getByRole('button', { name: 'Prepare environment: success' })).toBeVisible();
    await expect(graph.getByRole('button', { name: 'Check runtime: completed' })).toBeVisible();
    await expect(graph.getByRole('button', { name: 'Orchestrator: success' })).toHaveCount(0);
    await expect(graph.getByRole('button', { name: 'Execution 1: success' })).toHaveCount(0);
    await expect(graph.locator('[data-graph-edge][data-kind="fork"]')).toHaveCount(1);
    await expect(graph.locator('[data-graph-edge][data-kind="merge"]')).toHaveCount(0);
    await page.mouse.move(0, 0);
    await graph.screenshot({ path: '../.outputs/project-history/native-' + theme + '-' + width + '.png', animations: 'disabled' });
  }
});
