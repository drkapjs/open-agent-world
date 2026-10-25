import { expect, test, type Page } from '@playwright/test';
import { cp, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = fileURLToPath(new URL('../../', import.meta.url));
test.use({ actionTimeout: 15000 });

test.beforeEach(async ({ page, request }) => {
  const profile = await (await request.get('/api/application')).json();
  await request.patch('/api/application/preferences', { data: { profile_id: profile.profile_id, generation: profile.generation,
    changes: { 'oaw.locale': 'en', 'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }) } } });
  await page.setViewportSize({ width: 1700, height: 1100 });
});

async function openIDE(page: Page) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Pack and Card Library' }).click();
  await page.getByRole('button', { name: 'Pack IDE', exact: true }).click();
  const modal = page.getByRole('dialog', { name: 'Card Factory / Pack IDE', exact: true });
  await expect(modal).toBeVisible();
  return modal;
}

test('visual project designs, builds, installs and runs; external edits never overwrite a draft', async ({ page, request }, testInfo) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const ide = await openIDE(page);
  await ide.getByLabel('Project name', { exact: true }).fill('IDE Greeting');
  await ide.getByLabel('Pack ID', { exact: true }).fill('local.ide-greeting');
  const created = page.waitForResponse(response => response.url().endsWith('/api/packs/projects/create') && response.request().method() === 'POST');
  await ide.getByRole('button', { name: 'Create project', exact: true }).click();
  const project = await (await created).json();
  await expect(ide.getByText('Project contents', { exact: true })).toBeVisible();
  await ide.getByRole('button', { name: '选择油墨第 1 层', exact: true }).click();
  await ide.getByRole('button', { name: '设计标题', exact: true }).click();
  await ide.locator('.face-properties').getByLabel('标题', { exact: true }).fill('Made in Pack IDE');
  await ide.getByRole('button', { name: 'Function', exact: true }).click();
  await ide.getByLabel('Label', { exact: true }).fill('Your name');
  await ide.getByLabel(/^Output template/).fill('Hello {{name}} from Schema 3');
  await ide.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(ide.getByRole('status')).toContainText('Project files saved.');
  await ide.getByRole('button', { name: 'Check', exact: true }).click();
  await expect(ide.getByRole('status')).toContainText('Static project check passed');
  await ide.getByRole('button', { name: 'Preview', exact: true }).click();
  await ide.getByLabel('Your name', { exact: true }).fill('Visual author');
  await ide.getByRole('button', { name: '运行', exact: true }).click();
  await expect(ide.locator('output')).toContainText('Hello Visual author from Schema 3');
  await ide.screenshot({ path: testInfo.outputPath('pack-ide-preview.png') });
  const downloaded = page.waitForEvent('download');
  await ide.getByRole('button', { name: 'Build / Export', exact: true }).click();
  const artifact = await downloaded;
  expect(await artifact.failure()).toBeNull();
  const artifactPath = testInfo.outputPath(artifact.suggestedFilename());
  await artifact.saveAs(artifactPath);
  expect(artifact.suggestedFilename()).toBe('local.ide-greeting-0.1.0.oawpack');
  await expect(ide.getByRole('button', { name: 'Publish on Marketplace' })).toBeDisabled();

  // The actual source file is changed externally while Code has an unsaved edit.
  await ide.getByRole('button', { name: 'README.md', exact: true }).click();
  const diskPath = path.join(project.path, 'README.md');
  const original = await readFile(diskPath, 'utf8');
  await ide.getByLabel('Source code', { exact: true }).fill(original + '\nUnsaved editor draft\n');
  await writeFile(diskPath, original + '\nExternal editor change\n');
  await ide.getByRole('button', { name: 'Refresh project', exact: true }).click();
  await expect(ide.getByText('Files changed externally', { exact: true })).toBeVisible();
  await expect(ide.getByLabel('Source code')).toContainText('Unsaved editor draft');
  await expect(ide.getByRole('button', { name: 'Save', exact: true })).toBeDisabled();
  expect(await readFile(diskPath, 'utf8')).toContain('External editor change');
  await ide.screenshot({ path: testInfo.outputPath('pack-ide-conflict.png') });
  page.once('dialog', dialog => dialog.accept());
  await ide.getByRole('button', { name: 'Reload from disk', exact: true }).click();
  await ide.getByRole('button', { name: 'Close', exact: true }).click();

  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByLabel('Pack file', { exact: true }).setInputFiles(artifactPath);
  await library.getByRole('button', { name: 'Install Pack', exact: true }).click();
  await expect.poll(async () => (await (await request.get('/api/catalog')).json()).node_types.some(
    (node: { id: string }) => node.id === 'local.ide-greeting.card')).toBe(true);
  await library.getByRole('button', { name: 'Close Library', exact: true }).click();
  const createdCard = await request.post('/api/nodes', { data: { type: 'local.ide-greeting.card', position: { x: 400, y: 200 } } });
  expect(createdCard.status()).toBe(201);
  const card = await createdCard.json();
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await page.locator(`[data-card-id="${card.id}"]`).getByText('Made in Pack IDE', { exact: true }).click();
  const runtime = page.locator(`[data-workspace-node-id="${card.id}"]`);
  await runtime.getByLabel('Your name', { exact: true }).fill('Installed runtime');
  await runtime.getByRole('button', { name: '运行', exact: true }).click();
  await expect(runtime.locator('output')).toContainText('Hello Installed runtime from Schema 3');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  await expect.poll(async () => (await runtime.boundingBox())?.width ?? Infinity).toBeLessThan(1550);
  await page.screenshot({ path: testInfo.outputPath('pack-ide-installed-card.png') });
  expect(errors).toEqual([]);
});

test('custom frontend source opens, builds and previews through the installed runtime', async ({ page, request }, testInfo) => {
  test.setTimeout(120000);
  const temporary = path.join(root, '.tmp', `pack-ide-notes-source-${Date.now()}`);
  await mkdir(temporary, { recursive: true });
  await cp(path.join(root, 'examples/packs/notes'), temporary, { recursive: true });
  const sourcePath = path.join(temporary, 'frontend/index.tsx');
  const original = await readFile(sourcePath);
  execFileSync(process.execPath, ['--input-type=module', '-e',
    `import {buildPackFrontend} from ${JSON.stringify(new URL('../pack-sdk/build.mjs', import.meta.url).href)}; await buildPackFrontend(${JSON.stringify({ entryPoint: sourcePath, outfile: path.join(temporary, 'dist/frontend/index.js') })});`],
  { cwd: root, windowsHide: true });
  const ide = await openIDE(page);
  await ide.getByLabel('Project path', { exact: true }).fill(temporary);
  await ide.getByRole('button', { name: 'Open project', exact: true }).click();
  await ide.getByRole('button', { name: 'frontend/index.tsx', exact: true }).click();
  await expect(ide.getByLabel('Source code')).toContainText('data-note-output');
  await ide.getByRole('button', { name: 'Check', exact: true }).click();
  await expect(ide.getByRole('status')).toContainText('Static project check passed');
  const downloaded = page.waitForEvent('download');
  await ide.getByRole('button', { name: 'Build / Export', exact: true }).click();
  const artifact = await downloaded;
  expect(await artifact.failure()).toBeNull();
  const artifactPath = testInfo.outputPath(artifact.suggestedFilename());
  await artifact.saveAs(artifactPath);
  expect(await readFile(sourcePath)).toEqual(original);
  await ide.screenshot({ path: testInfo.outputPath('pack-ide-custom-code.png') });
  await ide.getByRole('button', { name: 'Close', exact: true }).click();
  const library = page.getByRole('dialog', { name: 'Pack & Card Library' });
  await library.getByLabel('Pack file', { exact: true }).setInputFiles(artifactPath);
  await library.getByRole('button', { name: 'Install Pack', exact: true }).click();
  await expect.poll(async () => (await (await request.get('/api/catalog')).json()).node_types.some(
    (node: { id: string }) => node.id === 'example.notes')).toBe(true);
  const created = await request.post('/api/nodes', { data: { type: 'example.notes', config: { message: 'Custom frontend retained' } } });
  expect(created.status()).toBe(201);
  await page.getByRole('button', { name: 'Pack IDE', exact: true }).click();
  await ide.getByLabel('Project path', { exact: true }).fill(temporary);
  await ide.getByRole('button', { name: 'Open project', exact: true }).click();
  await ide.getByRole('button', { name: 'Preview', exact: true }).click();
  await ide.getByLabel('Use installed card', { exact: true }).check();
  await expect(ide.locator('[data-note-output]')).toHaveText('Custom frontend retained');
  await ide.getByLabel('Note message', { exact: true }).fill('Edited through installed preview');
  await ide.getByRole('button', { name: 'Save note', exact: true }).click();
  await expect(ide.locator('[data-note-output]')).toHaveText('Edited through installed preview');
  await ide.screenshot({ path: testInfo.outputPath('pack-ide-custom-preview.png') });
  expect(await readFile(sourcePath)).toEqual(original);
});
