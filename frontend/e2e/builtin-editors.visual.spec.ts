import { expect, test, type APIRequestContext, type Locator, type Page } from '@playwright/test';

const output = '../.outputs/builtin-cards/after';

async function prepare(page: Page, request: APIRequestContext) {
  await page.setViewportSize({ width: 1600, height: 1100 });
  const profile = await (await request.get('/api/application')).json();
  const response = await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-node-surfaces-v1': null, 'oaw-canvas-viewport-v1': null,
    },
  } });
  expect(response.ok()).toBe(true);
}

async function openEditor(page: Page, id: string, name: string, opener: string) {
  await page.goto('/');
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  // These are content tests. Use the production opener without depending on
  // canvas camera coordinates or other cards overlapping the container header.
  await page.locator(`[data-card-id="${id}"]`).getByRole('button', { name: opener, exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  const surface = page.getByRole('dialog', { name: `${name} workspace`, exact: true });
  await expect(surface).toBeVisible();
  await expect(surface.getByRole('heading', { level: 3 })).toBeVisible();
  return surface;
}

async function resize(surface: Locator, width: number) {
  await surface.evaluate((element, value) => {
    const host = element as HTMLElement;
    Object.assign(host.style, { inset: '30px auto auto 30px', width: `${value}px`, height: '720px' });
    element.querySelector('.skill-toolbox')!.scrollTop = 0;
  }, width);
}

async function capture(page: Page, surface: Locator, slug: string) {
  for (const theme of ['light', 'dark']) {
    await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
    for (const width of [360, 1060]) {
      await resize(surface, width);
      const content = surface.locator('.skill-toolbox');
      const fit = await content.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
      expect(fit.scroll).toBeLessThanOrEqual(fit.width + 1);
      await surface.screenshot({ path: `${output}/${slug}-${theme}-${width}.png` });
    }
  }
}

test('Barracks settings save and reopen in narrow and wide containers', async ({ page, request }) => {
  test.setTimeout(120_000);
  await prepare(page, request);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const response = await request.post('/api/nodes', { data: {
    type: 'oaw.barracks', name: 'Research barracks', position: { x: 160, y: 180 },
  } });
  expect(response.status()).toBe(201);
  const card = await response.json();
  try {
    let surface = await openEditor(page, card.id, card.name, 'Open barracks');
    await expect(surface.getByText('Drag a configured Agent into this space to make it available.', { exact: true })).toBeVisible();
    await expect(surface.getByRole('button', { name: 'Summon new instance', exact: true })).toHaveCount(0);
    await resize(surface, 1060);
    await surface.screenshot({ path: `${output}/barracks-empty-light-1060.png` });
    await surface.getByRole('button', { name: 'Barracks settings', exact: true }).click();
    await surface.getByRole('textbox', { name: 'Instructions for connected Agents', exact: true }).fill('Read the source material and report the evidence behind each conclusion.');
    await surface.getByLabel('Maximum summon depth', { exact: true }).fill('3');
    await surface.getByLabel('Concurrent summoned Runs', { exact: true }).fill('2');
    await surface.getByLabel('Instances per root task', { exact: true }).fill('8');
    await resize(surface, 360);
    await surface.getByRole('button', { name: 'Save settings', exact: true }).click();
    await expect(surface.getByRole('textbox', { name: 'Instructions for connected Agents', exact: true })).toHaveCount(0);
    const saved = await (await request.get(`/api/nodes/${card.id}/document`)).json();
    expect(saved.value.instructions).toBe('Read the source material and report the evidence behind each conclusion.');
    expect(saved.value.policy).toMatchObject({ max_depth: 3, max_concurrent: 2, max_instances: 8 });
    await surface.getByRole('button', { name: 'Close barracks', exact: true }).click();
    surface = await openEditor(page, card.id, card.name, 'Open barracks');
    await surface.getByRole('button', { name: 'Barracks settings', exact: true }).click();
    await expect(surface.getByRole('textbox', { name: 'Instructions for connected Agents', exact: true })).toHaveValue(saved.value.instructions);
    await expect(surface.getByLabel('Maximum summon depth', { exact: true })).toHaveValue('3');
    await capture(page, surface, 'barracks-settings');
    await surface.getByRole('button', { name: 'Cancel', exact: true }).click();
    await capture(page, surface, 'barracks');
    expect(errors).toEqual([]);
  } finally {
    await request.post('/api/nodes/batch-delete', { data: { node_ids: [card.id] } });
  }
});

test('Skill Toolbox creates and edits durable instructions through its real editor', async ({ page, request }) => {
  test.setTimeout(120_000);
  await prepare(page, request);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const response = await request.post('/api/nodes', { data: {
    type: 'oaw.skills', name: 'Research skills', position: { x: 160, y: 180 },
  } });
  expect(response.status()).toBe(201);
  const card = await response.json();
  const documentUrl = `/api/nodes/${card.id}/document`;
  try {
    let surface = await openEditor(page, card.id, card.name, 'Edit toolbox');
    await expect(surface.getByRole('button', { name: 'Add skill', exact: true })).toBeEnabled();
    await resize(surface, 1060);
    await surface.screenshot({ path: `${output}/skills-empty-light-1060.png` });
    await surface.getByRole('button', { name: 'Add skill', exact: true }).click();
    await surface.getByLabel('Skill name', { exact: true }).fill('Review evidence');
    await surface.getByLabel('When to use', { exact: true }).fill('Before publishing research findings.');
    await surface.getByRole('textbox', { name: 'Instructions', exact: true }).fill('Read each source, compare the reported measurements, and cite the strongest evidence.');
    await surface.getByRole('button', { name: 'Save skill', exact: true }).click();
    const edit = () => surface.getByRole('button', { name: 'Edit skill Review evidence', exact: true });
    await expect(edit()).toBeEnabled();
    let saved = await (await request.get(documentUrl)).json();
    expect(saved.value.skills[0].instructions).toContain('compare the reported measurements');
    await surface.getByRole('button', { name: 'Close toolbox editor', exact: true }).click();
    surface = await openEditor(page, card.id, card.name, 'Edit toolbox');
    await edit().click();
    await expect(surface.getByRole('textbox', { name: 'Instructions', exact: true })).toHaveValue(saved.value.skills[0].instructions);
    await resize(surface, 360);
    await expect(surface.getByRole('button', { name: 'Add skill', exact: true })).toHaveCount(0);
    await expect(surface.locator('.toolbox-tools')).toHaveCount(1);
    await expect(surface.locator('.toolbox-tools')).toBeHidden();
    await surface.getByRole('textbox', { name: 'Instructions', exact: true }).fill('Compare the source measurements. Record uncertainty, cite the evidence, and explain any disagreement.');
    await surface.getByRole('button', { name: 'Save skill', exact: true }).click();
    await expect(edit()).toBeEnabled();
    saved = await (await request.get(documentUrl)).json();
    expect(saved.value.skills[0].instructions).toContain('Record uncertainty');
    await capture(page, surface, 'skills');
    await edit().click();
    await expect(surface.getByRole('textbox', { name: 'Instructions', exact: true })).toHaveValue(saved.value.skills[0].instructions);
    await capture(page, surface, 'skills-editor');
    await surface.getByRole('button', { name: 'Close skill details', exact: true }).click();
    await expect(edit()).toBeEnabled();
    expect(errors).toEqual([]);
  } finally {
    const latest = await (await request.get(documentUrl)).json();
    const members = (latest.value?.skills ?? []).map((skill: { node_id?: string }) => skill.node_id).filter(Boolean);
    await request.post('/api/nodes/batch-delete', { data: { node_ids: [card.id, ...members] } });
  }
});
