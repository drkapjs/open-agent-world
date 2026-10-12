import { createContext, useContext } from 'react';
import type { WorkspaceView } from '../legions/workspaceLayout';

/** Browser-local presentation intent, scoped to the currently mounted workspace. */
export interface WorkspaceNavigation {
  /** Reveal a placed tab/section or an unplaced card's drawer. False means unavailable.
   * Never changes saved geometry, restores hidden sections or opens another workspace. */
  activate(view: WorkspaceView): boolean;
}

export const WorkspaceNavigationContext = createContext<WorkspaceNavigation | undefined>(undefined);
export const useWorkspaceNavigation = () => useContext(WorkspaceNavigationContext);
