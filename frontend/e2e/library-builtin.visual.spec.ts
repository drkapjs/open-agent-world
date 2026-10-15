import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const phase = process.env.OAW_BUILTIN_CAPTURE_PHASE ?? 'after';
const output = `../.outputs/builtin-cards/${phase}`;

// A real, self-contained PDF parsed by the server and PDF.js. Only the document
// content is a fixture; the card, import, annotations and reader use production APIs.
function pdfFixture() {
  const stream = [
    'BT /F1 22 Tf 55 730 Td (Lithium transport in solid electrolytes) Tj',
    '/F1 11 Tf 0 -28 Td (Research notes - built-in reader visual fixture) Tj',
    '/F1 15 Tf 0 -48 Td (Results) Tj',
    '/F1 12 Tf 0 -25 Td (Conductivity increased with temperature in the measured samples.) Tj',
    '0 -22 Td (Each observation remains linked to the source document.) Tj',
    '/F1 15 Tf 0 -48 Td (Sample measurements) Tj',
    '/F1 12 Tf 0 -28 Td (Sample                 Temperature       Conductivity) Tj',
    '0 -25 Td (Li6PS5Cl                300 K             1.2 mS/cm) Tj',
    '0 -25 Td (Li6PS5Br                300 K             0.8 mS/cm) Tj',
    '/F1 15 Tf 0 -48 Td (Method) Tj',
    '/F1 12 Tf 0 -25 Td (Impedance spectroscopy was used to measure the pellets.) Tj',
    '0 -22 Td (Compare the temperature range before combining measurements.) Tj',
    '/F1 15 Tf 0 -48 Td (Next steps) Tj',
    '/F1 12 Tf 0 -25 Td (Review the sample conditions and add an evidence note.) Tj ET',
  ].join('\n');
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`,
  ];
  let pdf = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((object, index) => {
    offsets.push(pdf.length);
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`;
  });
  const xref = pdf.length;
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.map(offset => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf).toString('base64');
}

async function action(request: APIRequestContext, id: string, name: string, args: Record<string, unknown>) {
  const documentResponse = await request.get(`/api/nodes/${id}/document`);
  expect(documentResponse.ok(), await documentResponse.text()).toBe(true);
  const document = await documentResponse.json();
  const response = await request.post(`/api/nodes/${id}/actions/${name}`, {
    data: { expected_revision: document.revision, arguments: args },
  });
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

async function openReader(page: Page, id: string) {
  await page.getByRole('button', { name: 'Fit view', exact: true }).click();
  const card = page.locator(`[data-card-id="${id}"]`);
  await expect(card).toBeVisible();
  if (await card.getAttribute('data-surface-level') === 'preview') await card.dblclick();
  await expect(card.locator('.library-magazine')).toBeVisible();
  // Trigger the mounted card's real action independently of canvas zoom or
  // overlapping windows; reader interactions below use ordinary pointer clicks.
  await card.locator('.library-magazine-intro').getByRole('button', { name: 'Open reader', exact: true })
    .evaluate((button: HTMLButtonElement) => button.click());
  const reader = page.locator('dialog.library-reader');
  await expect(reader).toHaveAttribute('data-entrance-phase', 'complete');
  await expect(reader.locator('.textLayer')).toContainText('Conductivity increased');
  return reader;
}

test('library PDF import, reading controls and study states at real window sizes', async ({ page, request }) => {
  test.setTimeout(90_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const profile = await (await request.get('/api/application')).json();
  expect((await request.patch('/api/application/preferences', { data: {
    profile_id: profile.profile_id, generation: profile.generation, changes: {
      'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
      'oaw.locale': 'en', 'oaw-theme': 'light', 'oaw-node-surfaces-v1': null,
      'oaw-canvas-viewport-v1': null,
    },
  } })).ok()).toBe(true);
  const response = await request.post('/api/nodes', { data: {
    type: 'library.paper', name: 'Solid electrolyte research notes',
    position: { x: 300, y: 250 }, config: { authors: 'OAW research team', year: '2026' },
  } });
  expect(response.status(), await response.text()).toBe(201);
  const { id } = await response.json();
  try {
    const imported = await action(request, id, 'import', { filename: 'solid-electrolytes.pdf', pdf: pdfFixture() });
    expect(imported.value.pages).toBe(1);
    expect(imported.value.thumbnail).toMatch(/^data:image\/png;base64,/);
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('/');
    const reader = await openReader(page, id);
    await expect(reader.locator('.library-reading-controls')).toHaveCount(1);

    for (const theme of ['light', 'dark']) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      for (const width of [1440, 640]) {
        await page.setViewportSize({ width, height: 900 });
        await expect(reader).toHaveAttribute('data-entrance-phase', 'complete');
        await expect(reader.locator('.textLayer')).toContainText('Conductivity increased');
        const controls = reader.locator('.library-reading-controls');
        const bounds = await controls.evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
        expect(bounds.scroll).toBeLessThanOrEqual(bounds.width + 1);
        await reader.screenshot({ path: `${output}/library-reader-${theme}-${width}.png` });
      }
    }

    await reader.getByRole('button', { name: 'Study canvas', exact: true }).click();
    await expect(reader.locator('.library-study-empty')).toContainText('No study cards yet');
    await expect(reader.locator('.library-reading-controls')).toHaveCount(0);
    await reader.screenshot({ path: `${output}/library-study-empty-dark-640.png` });
    await reader.locator('.library-study-controls').getByRole('button', { name: 'Back to paper', exact: true }).click();
    await expect(reader.locator('.library-study')).toHaveCount(0);
    await expect(reader.locator('.library-reading-controls')).toBeVisible();
    await reader.getByRole('button', { name: 'Back to window', exact: true }).click();
    await expect(reader).toHaveCount(0);

    await action(request, id, 'annotate', { study_title: 'Transport evidence', annotation: {
      id: 'transport-evidence', page: 1, title: 'Temperature dependence',
      text: 'Conductivity increased with temperature in the measured samples.',
      comment: 'Compare the sample conditions before combining measurements.',
      rects: [[0.09, 0.18, 0.79, 0.024]], learning: true, position: { x: 20, y: 80 },
    } });
    // Reload the real UI after the external API mutation to refresh its paper cache.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.reload();
    const populated = await openReader(page, id);
    await populated.getByRole('button', { name: 'Study canvas', exact: true }).click();
    await expect(populated.locator('.library-reading-controls')).toHaveCount(0);
    const studyCard = populated.locator('[data-study-id="transport-evidence"]');
    await expect(studyCard.getByRole('textbox', { name: 'Excerpt title', exact: true })).toHaveValue('Temperature dependence');
    await expect(studyCard.locator('blockquote')).toContainText('Conductivity increased');
    for (const [theme, width] of [['light', 1440], ['dark', 640]] as const) {
      await page.evaluate(value => { document.documentElement.dataset.theme = value; }, theme);
      await page.setViewportSize({ width, height: 900 });
      await populated.screenshot({ path: `${output}/library-study-populated-${theme}-${width}.png` });
    }
    await populated.getByRole('button', { name: 'Back to window', exact: true }).click();
    await expect(populated).toHaveCount(0);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await expect(page.locator(`[data-card-id="${id}"] .library-magazine-notes`)).toContainText('Conductivity increased');
    await page.screenshot({ path: `${output}/library-paper-canvas.png` });
    expect(errors).toEqual([]);
  } catch (error) {
    await page.screenshot({ path: `${output}/library-failure.png` });
    throw error;
  } finally {
    expect((await request.post('/api/nodes/batch-delete', { data: { node_ids: [id] } })).ok()).toBe(true);
  }
});
