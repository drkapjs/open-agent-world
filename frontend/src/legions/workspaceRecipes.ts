import { layoutMinimum, readWorkspaceLayout, viewKey, type WorkspaceLayout, type WorkspaceNode, type WorkspaceView } from './workspaceLayout';
import { workspaceLayoutSize, type WorkspaceLayoutMetadata } from '../workspace/workspaceLayoutMetadata';

export type WorkspaceRecipe = 'balanced' | 'columns' | 'rows' | 'focus';
export interface WorkspaceCompositionItem { view: WorkspaceView; layout?: WorkspaceLayoutMetadata }
export interface WorkspaceCompositionOptions {
  recipe?: WorkspaceRecipe;
  width?: number;
  height?: number;
}

/**
 * One-shot recipes produce the existing v2 split tree. Input order is explicit;
 * no card type, running state, or content decides which view is important.
 * Every supplied view stays visible. Tabs remain an explicit docking choice.
 */
export function composeWorkspace(items: readonly WorkspaceCompositionItem[], options: WorkspaceCompositionOptions = {}): WorkspaceLayout {
  if (items.length > 100) throw new RangeError('Workspace layouts support at most 100 views.');
  const unique = new Set(items.map(item => viewKey(item.view)));
  if (unique.size !== items.length) throw new Error('Workspace views must be unique.');
  const metadata = new Map(items.map(item => [viewKey(item.view), item.layout]));
  const preference = (view: WorkspaceView) => metadata.get(viewKey(view));
  const recipe = options.recipe ?? 'balanced';
  const dimension = (value: number | undefined, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : fallback;

  function build(group: readonly WorkspaceCompositionItem[], width: number, height: number, depth = 0): WorkspaceNode {
    if (group.length === 1) return { kind: 'pane', view: { ...group[0].view } };
    const axis = recipe === 'columns' ? 'horizontal' : recipe === 'rows' ? 'vertical'
      : width >= height * 1.2 ? 'horizontal' : 'vertical';
    const sizes = group.map(item => workspaceLayoutSize(item.layout).ideal);
    const weights = sizes.map(size => size.width * size.height);
    const total = weights.reduce((sum, value) => sum + value, 0);
    // Keep depth bounded for the full supported 100 views, even for extreme hints.
    const middle = Math.ceil(group.length / 2);
    const index = recipe === 'focus' && depth === 0 ? 1 : middle;
    const firstWeight = weights.slice(0, index).reduce((sum, value) => sum + value, 0);
    let ratio = recipe === 'focus' && depth === 0 ? .65 : Math.min(.8, Math.max(.2, firstWeight / total));
    const first = build(group.slice(0, index), axis === 'horizontal' ? width * ratio : width,
      axis === 'vertical' ? height * ratio : height, depth + 1);
    const second = build(group.slice(index), axis === 'horizontal' ? width * (1 - ratio) : width,
      axis === 'vertical' ? height * (1 - ratio) : height, depth + 1);
    const a = layoutMinimum(first, preference), b = layoutMinimum(second, preference);
    const key = axis === 'horizontal' ? 'width' : 'height';
    const available = (axis === 'horizontal' ? width : height) - 5;
    if (available >= a[key] + b[key]) {
      ratio = Math.max(a[key] / available, Math.min(1 - b[key] / available, ratio));
    }
    return { kind: 'split', axis, ratio: Math.min(.85, Math.max(.15, ratio)), first, second };
  }

  const root = items.length ? build(items, dimension(options.width, 1280), dimension(options.height, 800)) : null;
  const result = readWorkspaceLayout({ version: 2, root, hidden_sections: [] });
  if (root && !result.root) throw new Error('Workspace views must use valid card and section identifiers.');
  return result;
}
