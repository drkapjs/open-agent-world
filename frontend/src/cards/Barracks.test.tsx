// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { worldApi } from '../api/client';
import { useNodeSurfaceStore } from '../state/nodeSurfaces';
import { useWorldStore } from '../state/worldStore';
import type { WorldCard } from '../types/world';
import { BarracksBody, type SummoningSnapshot } from './Barracks';

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('keeps follow-up available for an existing instance after its blueprint leaves', async () => {
  const card: WorldCard = { id: 'barracks', type: 'oaw.barracks', name: 'Research barracks', status: 'available',
    config: {}, position: { x: 0, y: 0 }, size: { width: 960, height: 600 }, expanded: true };
  const snapshot: SummoningSnapshot = { agents: [], instructions: '', policy: { max_depth: 4, max_concurrent: 4, max_instances: 16 },
    instances: [{ id: 'existing-instance', name: 'Researcher', status: 'succeeded', result: 'Initial findings',
      error: null, entry_agent_id: 'retained-agent', node_ids: ['retained-agent'], run_id: 'retained-run', reclaimed: false }] };
  useNodeSurfaceStore.setState({ drafts: {} });
  useWorldStore.setState({ cards: [card], events: [], socketState: 'live' });
  vi.spyOn(worldApi, 'getSummoning').mockResolvedValue(snapshot);
  const action = vi.spyOn(worldApi, 'summoningAction').mockResolvedValue(snapshot.instances[0]);
  vi.spyOn(useWorldStore.getState(), 'refreshWorld').mockResolvedValue(undefined);
  render(<BarracksBody card={card} workspace />);
  fireEvent.change(await screen.findByRole('textbox', { name: 'Task / follow-up' }), { target: { value: 'Continue the remaining research.' } });
  expect(screen.queryByRole('button', { name: 'Summon new instance' })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Follow up' }));
  await waitFor(() => expect(action).toHaveBeenCalledWith(card.id, expect.objectContaining({
    action: 'message', instance_id: 'existing-instance', prompt: 'Continue the remaining research.',
  })));
});
