/** Optional presentation hints. They never change card data or runtime ownership. */
export interface WorkspaceLayoutMetadata {
  min_width?: number;
  min_height?: number;
  ideal_width?: number;
  ideal_height?: number;
}

export interface WorkspaceLayoutSize {
  minimum: { width: number; height: number };
  ideal: { width: number; height: number };
}

/** Old packs and invalid JavaScript declarations retain usable, neutral defaults. */
export function workspaceLayoutSize(layout?: WorkspaceLayoutMetadata): WorkspaceLayoutSize {
  const dimension = (value: number | undefined, fallback: number) =>
    typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.max(96, Math.min(8192, value)) : fallback;
  const minimum = { width: dimension(layout?.min_width, 240), height: dimension(layout?.min_height, 200) };
  return { minimum, ideal: {
    width: Math.max(minimum.width, dimension(layout?.ideal_width, 480)),
    height: Math.max(minimum.height, dimension(layout?.ideal_height, 360)),
  } };
}
