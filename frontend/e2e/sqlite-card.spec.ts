import { expect, test } from "@playwright/test";

test("SQL card inspects schema, runs SQL, confirms deletion and survives reload", async ({ page, request }) => {
  await page.setViewportSize({ width: 1600, height: 1000 });
  const api = "http://127.0.0.1:8017/api";
  const created = await request.post(`${api}/nodes`, { data: { type: "data.sqlite", name: "Research database", position: { x: 650, y: 450 } } });
  expect(created.ok()).toBeTruthy();
  const card = await created.json();
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  try {
    await page.goto("/");
    const surface = page.locator(`[data-card-id="${card.id}"]`);
    await expect(surface).toBeVisible();
    await surface.click({ position: { x: 140, y: 30 } });
    await surface.getByRole("button", { name: "Open workspace", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Research database workspace", exact: true });
    await expect(dialog.getByLabel("SQL editor")).toBeVisible();
    await dialog.getByLabel("Read only", { exact: true }).uncheck();
    await dialog.getByLabel("SQL editor").fill("CREATE TABLE samples (id INTEGER PRIMARY KEY, name TEXT NOT NULL, value REAL)");
    await dialog.getByRole("button", { name: "Run SQL", exact: true }).click();
    await expect(dialog.getByRole("option", { name: "samples · table" })).toBeAttached();
    await dialog.getByLabel("SQL editor").fill("INSERT INTO samples VALUES (1, 'Control', 12.4), (2, 'Treatment A', 18.7), (3, 'Treatment B', 21.2)");
    await dialog.getByRole("button", { name: "Run SQL", exact: true }).click();
    await expect(dialog.getByRole("status")).toContainText("3 changes");
    await dialog.getByLabel("Tables & views", { exact: true }).selectOption('samples');
    await expect(dialog.getByRole("tabpanel", { name: "Table schema" })).toContainText("PRIMARY KEY");
    await dialog.getByRole("button", { name: "Browse rows" }).click();
    await expect(dialog.getByRole("cell", { name: "Treatment A", exact: true })).toBeVisible();
    const foreground = await dialog.locator(".sqlite-app").evaluate(element => getComputedStyle(element).color);
    expect(foreground).toBe("rgb(53, 51, 46)");
    await page.screenshot({ path: "../.outputs/sqlite-card.png", fullPage: true });
    await page.evaluate(() => document.documentElement.dataset.theme = "dark");
    await expect(dialog.locator(".sqlite-app")).toHaveCSS("color", "rgb(233, 230, 222)");
    await page.screenshot({ path: "../.outputs/sqlite-card-dark.png", fullPage: true });
    await page.evaluate(() => document.documentElement.dataset.theme = "light");
    await dialog.getByLabel("SQL editor").fill("DELETE FROM samples WHERE id = 3");
    await dialog.getByRole("button", { name: "Run SQL", exact: true }).click();
    await expect(dialog.getByText("Confirm database change", { exact: true })).toBeVisible();
    const before = await (await request.post(`${api}/nodes/${card.id}/resource/query`, { data: { arguments: { sql: "SELECT count(*) FROM samples" } } })).json();
    expect(before.rows).toEqual([[3]]);
    await dialog.getByRole("button", { name: "Confirm and run" }).click();
    await expect(dialog.getByRole("status")).toContainText("1 changes");
    await page.reload();
    const after = await (await request.post(`${api}/nodes/${card.id}/resource/query`, { data: { arguments: { sql: "SELECT name FROM samples ORDER BY id" } } })).json();
    expect(after.rows).toEqual([["Control"], ["Treatment A"]]);
    expect(errors).toEqual([]);
  } finally {
    await request.delete(`${api}/nodes/${card.id}`);
  }
});
