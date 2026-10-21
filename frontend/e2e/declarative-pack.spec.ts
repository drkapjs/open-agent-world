import { expect, test, type Page } from '@playwright/test';
import { readFile, writeFile } from 'node:fs/promises';

test.skip(process.env.OAW_PACK_DECLARATIVE !== '1', 'Use run-pack-e2e.mjs --declarative');

async function openWorld(page: Page) {
  const state = await (await page.request.get('/api/application')).json();
  const saved = state.values['oaw-onboarding-v1'];
  const first = !saved || JSON.parse(saved).state?.status === 'new';
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Open Pack and Card Library' })).toBeVisible();
  if (first) await page.getByRole('button', { name: 'Start blank', exact: true }).click();
}

test('install local declarative Pack through the existing Library installer', async ({ page }) => {
  await openWorld(page);
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByLabel('Pack file', { exact: true }).setInputFiles(process.env.OAW_PACK_ARTIFACT!);
  const installed = page.waitForResponse(response => response.url().endsWith('/api/packs/install') && response.request().method() === 'POST');
  await library.getByRole('button', { name: 'Install Pack', exact: true }).click();
  const response = await installed;
  expect(response.status()).toBe(201);
  await expect.poll(async () => {
    const status = await (await page.request.get('/api/packs')).json();
    return status.versions.some((v: { id: string; selected: boolean }) => v.id === 'example.notes' && v.selected);
  }).toBe(true);
  // Existing page, no navigation or reload: exercise the Library update event.
  await expect(library.locator('[data-pack-id="example.notes.default"]')).toBeVisible();
  expect((await (await page.request.get('/api/packs')).json()).restart_required).toBe(false);
});

test('install activates declarative UI without restarting the host and saves configuration', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await openWorld(page);
  const catalog = await (await request.get('/api/catalog')).json();
  expect((await (await request.get('/api/packs')).json()).restart_required).toBe(false);
  expect(catalog.node_types.some((n: { id: string }) => n.id === 'example.notes')).toBe(true);
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  const pack = library.locator('[data-pack-id="example.notes.default"]');
  await pack.getByRole('button', { name: 'Tear open Notes', exact: true }).click();
  await pack.getByRole('button', { name: 'View pack Notes', exact: true }).click();
  await library.getByRole('button', { name: 'View cards', exact: true }).click();
  await library.getByRole('button', { name: 'Add Notes to deck', exact: true }).click();
  await library.getByRole('button', { name: 'Close Library', exact: true }).click();
  const tray = page.getByRole('complementary', { name: 'Active card deck' });
  await tray.hover();
  await tray.getByRole('button', { name: 'Place Notes', exact: true }).dragTo(page.locator('.react-flow__pane').first(), { targetPosition: { x: 620, y: 320 } });
  await page.getByRole('article', { name: 'Notes Notes', exact: true }).click();
  await page.getByLabel('Note message', { exact: true }).fill('Persisted declarative note');
  await page.getByRole('button', { name: 'Save note', exact: true }).click();
  await expect(page.locator('[data-note-output]')).toHaveText('Persisted declarative note');
  const cards = await (await request.get('/api/nodes')).json();
  const card = cards.find((n: { type: string }) => n.type === 'example.notes');
  await writeFile(`${process.env.OAW_PACK_E2E_DATA_ROOT}/note-state.json`, JSON.stringify(card));
  await page.reload();
  await expect(page.locator('[data-note-output]')).toHaveText('Persisted declarative note');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${process.env.OAW_PACK_E2E_DATA_ROOT}/declarative-note.png` });
  expect(errors).toEqual([]);
});

test('saved note survives another real host restart with the same identity and geometry', async ({ page, request }) => {
  await openWorld(page);
  await expect(page.locator('[data-note-output]')).toHaveText('Persisted declarative note');
  const previous = JSON.parse(await readFile(`${process.env.OAW_PACK_E2E_DATA_ROOT}/note-state.json`, 'utf8'));
  const card = await (await request.get(`/api/nodes/${previous.id}`)).json();
  for (const key of ['id', 'type', 'plugin_id', 'config', 'position', 'size']) expect(card[key]).toEqual(previous[key]);
});
