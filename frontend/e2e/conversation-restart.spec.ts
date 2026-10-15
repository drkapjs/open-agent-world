import { expect, test } from '@playwright/test';

test('edit from a message forks the prefix and sends only in the new session', async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const room = await (await request.post('/api/nodes', { data: {
    type: 'conversation', name: 'Restart QA', position: { x: 800, y: 450 },
  } })).json();
  const group = (await (await request.post('/api/legion-groups', { data: {
    name: 'Restart studio', node_ids: [room.id],
  } })).json())[0];
  await request.patch(`/api/nodes/${group.id}`, { data: { config: { workspace_layout: {
    version: 2, hidden_sections: [], root: { kind: 'split', axis: 'vertical', ratio: .25,
      first: { kind: 'pane', view: { card_id: room.id, section_id: 'sessions' } },
      second: { kind: 'pane', view: { card_id: room.id, section_id: 'conversation' } },
    },
  } } } });
  const base = `/api/conversations/${room.id}`;
  try {
    const session = await (await request.post(`${base}/sessions`, { data: { title: 'Original' } })).json();
    const prefix = `${base}/sessions/${session.id}`;
    const ids: string[] = [];
    for (const content of ['Earlier context', 'Original question', 'Later context to exclude']) {
      ids.push((await (await request.post(`${prefix}/messages`, { data: { content } })).json()).message.id);
    }
    const profile = await (await request.get('/api/application')).json();
    await request.patch('/api/application/preferences', { data: {
      profile_id: profile.profile_id, generation: profile.generation, changes: {
        'oaw-onboarding-v1': JSON.stringify({ version: 1, state: { status: 'skipped' } }),
        'oaw.locale': 'en',
      },
    } });
    await page.goto('/');
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.getByRole('region', { name: 'Restart studio team space' }).getByRole('button', { name: 'Workspace mode', exact: true })
      .evaluate((button: HTMLButtonElement) => button.click());
    const workspace = page.getByRole('dialog', { name: 'Restart studio workspace mode' });
    const message = workspace.locator(`[data-message-id="${ids[1]}"]`);
    const actions = message.locator('.conversation-message-actions');
    await expect(actions).toHaveCSS('opacity', '0');
    await message.hover();
    await expect(actions).toHaveCSS('opacity', '1');
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await message.getByRole('button', { name: 'Copy message', exact: true }).click();
    await expect(message.getByRole('button', { name: 'Message copied', exact: true })).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('Original question');
    await page.screenshot({ path: '../.outputs/conversation-message-actions.png' });
    await workspace.getByLabel('Conversation message').focus();
    await workspace.getByLabel('Conversation message').hover();
    await expect(actions).toHaveCSS('opacity', '0');
    await message.getByRole('button', { name: 'Branch in new chat' }).focus();
    await expect(actions).toHaveCSS('opacity', '1');
    await message.getByRole('button', { name: 'Branch in new chat' }).click();
    const composer = workspace.getByLabel('Conversation message');
    await expect(composer).toHaveValue('Original question');
    await expect(workspace.getByText('Earlier context', { exact: true })).toBeVisible();
    await expect(workspace.getByText('Later context to exclude', { exact: true })).toHaveCount(0);
    await page.screenshot({ path: '../.outputs/conversation-restart-desktop.png' });
    await page.setViewportSize({ width: 820, height: 900 });
    await expect(composer).toBeInViewport();
    await page.screenshot({ path: '../.outputs/conversation-restart-narrow.png' });
    await composer.fill('Revised question');
    await workspace.getByRole('button', { name: 'Send message', exact: true }).click();
    await expect(workspace.getByText('Revised question', { exact: true })).toBeVisible();
    const summary = await (await request.get(base)).json();
    const branch = summary.sessions.find((item: { restart_before_message_id?: string }) => item.restart_before_message_id === ids[1]);
    expect(branch.id).not.toBe(session.id);
    const messages = await (await request.get(`${base}/sessions/${branch.id}/messages`)).json();
    expect(messages.map((item: { content: string }) => item.content)).toEqual(['Earlier context', 'Revised question']);
    expect((await (await request.get(`${prefix}/messages`)).json()).map((item: { id: string }) => item.id)).toEqual(ids);
    await page.reload();
    await page.getByRole('button', { name: 'Fit view', exact: true }).click();
    await page.getByRole('region', { name: 'Restart studio team space' }).getByRole('button', { name: 'Workspace mode', exact: true })
      .evaluate((button: HTMLButtonElement) => button.click());
    await expect(workspace.getByText('Revised question', { exact: true })).toBeVisible();
  } finally {
    await request.delete(`/api/nodes/${room.id}`);
    await request.delete(`/api/nodes/${group.id}`);
  }
});
