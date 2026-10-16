import { describe, expect, it } from 'vitest';
import { composeWorkspace, type WorkspaceCompositionItem, type WorkspaceRecipe } from './workspaceRecipes';
import { layoutMinimum, paneViews, readWorkspaceLayout, type WorkspaceNode } from './workspaceLayout';
import { workspaceLayoutSize } from '../workspace/workspaceLayoutMetadata';

const items: WorkspaceCompositionItem[] = ['graph', 'files', 'terminal', 'chart'].map(card_id => ({ view: { card_id } }));

describe('one-shot workspace composition', () => {
  it('keeps every input visible in every recipe without mutating the input', () => {
    const original = JSON.stringify(items);
    for (const recipe of ['balanced', 'columns', 'rows', 'focus'] as WorkspaceRecipe[]) {
      const layout = composeWorkspace(items, { recipe });
      expect(paneViews(layout.root)).toEqual(items.map(item => item.view));
      expect(JSON.stringify(layout)).not.toContain('"tabs"');
      expect(layout.hidden_sections).toEqual([]);
      expect(readWorkspaceLayout(layout)).toEqual(layout);
    }
    expect(JSON.stringify(items)).toBe(original);
    expect(composeWorkspace([]).root).toBeNull();
    expect(composeWorkspace([items[0]]).root).toEqual({ kind: 'pane', view: items[0].view });
  });

  it('uses actual available size once while saved layout proportions remain untouched', () => {
    expect(composeWorkspace(items, { width: 1400, height: 800 }).root).toMatchObject({ axis: 'horizontal' });
    expect(composeWorkspace(items, { width: 600, height: 900 }).root).toMatchObject({ axis: 'vertical' });
    const saved = composeWorkspace(items, { width: 1400, height: 800 });
    const serialized = JSON.stringify(saved);
    composeWorkspace(items, { width: 600, height: 900 });
    expect(JSON.stringify(saved)).toBe(serialized);
  });

  it('allocates space by declared needs without knowing card types or names', () => {
    const layout = composeWorkspace([
      { view: { card_id: 'arbitrary-large' }, layout: { ideal_width: 800, ideal_height: 500 } },
      { view: { card_id: 'arbitrary-small' }, layout: { ideal_width: 320, ideal_height: 300 } },
    ], { width: 1600, height: 800 });
    expect(layout.root).toMatchObject({ kind: 'split', axis: 'horizontal' });
    expect((layout.root as Extract<WorkspaceNode, { kind: 'split' }>).ratio).toBeGreaterThan(.6);
  });

  it('keeps the maximum tab minimum stable when a smaller tab becomes active', () => {
    const root: WorkspaceNode = { kind: 'tabs', views: [items[0].view, items[1].view], active_view: items[0].view };
    const preference = (view: { card_id: string }) => view.card_id === 'graph' ? { min_width: 560, min_height: 360 } : undefined;
    expect(layoutMinimum(root, preference)).toEqual({ width: 560, height: 360 });
    expect(layoutMinimum({ ...root, active_view: items[1].view }, preference)).toEqual(layoutMinimum(root, preference));
  });

  it('supports all 100 views even with extreme sizing without exceeding saved-layout depth limits', () => {
    const many = Array.from({ length: 100 }, (_, index) => ({ view: { card_id: `card-${index}` },
      layout: index % 2 ? { ideal_width: 8192, ideal_height: 8192 } : { ideal_width: 96, ideal_height: 96 } }));
    for (const recipe of ['balanced', 'columns', 'rows', 'focus'] as WorkspaceRecipe[]) {
      const layout = composeWorkspace(many, { recipe });
      expect(paneViews(readWorkspaceLayout(layout).root)).toHaveLength(100);
    }
    expect(() => composeWorkspace([...many, { view: { card_id: 'overflow' } }])).toThrow(/100/);
    expect(() => composeWorkspace([items[0], items[0]])).toThrow(/unique/);
    expect(() => composeWorkspace([{ view: { card_id: '' } }])).toThrow(/identifiers/);
  });

  it('provides neutral legacy defaults and safe minimum sizes for untyped JavaScript packs', () => {
    expect(workspaceLayoutSize()).toEqual({ minimum: { width: 240, height: 200 }, ideal: { width: 480, height: 360 } });
    expect(workspaceLayoutSize({ min_width: NaN, min_height: -1, ideal_width: Infinity })).toEqual(workspaceLayoutSize());
    expect(workspaceLayoutSize({ min_width: 600, ideal_width: 300 }).ideal.width).toBe(600);
    expect(workspaceLayoutSize({ min_width: 1e9, min_height: 1 }).minimum).toEqual({ width: 8192, height: 96 });
  });
});
