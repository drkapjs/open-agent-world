// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import { ContainerCardNode } from './ContainerCard';
import { TEST_CATALOG } from '../state/catalog.fixture';
import { useWorldStore } from '../state/worldStore';

vi.mock('./CardFrame', () => ({ WorldCardNode: ({ data }: any) => <div role="status">Recoverable card: {data.card.name}</div> }));
afterEach(cleanup);

it('keeps a stale container render recoverable while its Pack definition is absent', () => {
  useWorldStore.setState({ catalog: { ...TEST_CATALOG, node_types: [] } });
  const props = { id: 'container', type: 'container', data: { card: { id: 'container', name: 'Research toolbox', type: 'oaw.skills', config: {}, position: { x: 0, y: 0 }, status: 'idle' }, surfaceLevel: 'workspace' } } as ComponentProps<typeof ContainerCardNode>;
  render(<ContainerCardNode {...props} />);
  expect(screen.getByRole('status').textContent).toContain('Research toolbox');
});
