import { useId, type CSSProperties, type ReactNode } from 'react';
import './workspacePrimitives.css';

/** Optional content wrapper, independent of card material and canvas geometry. */
export function ApplicationSurface({ children, appearance = 'warm', className = '', style }: {
  children: ReactNode; appearance?: 'warm' | 'paper'; className?: string; style?: CSSProperties;
}) {
  return <div className={`oaw-application ${className}`} data-appearance={appearance} style={style}>{children}</div>;
}

/** A wrapping action row. Native button, input and select semantics are retained. */
export function WorkspaceToolbar({ label, children, className = '' }: {
  label: string; children: ReactNode; className?: string;
}) {
  return <div className={`oaw-toolbar ${className}`} role="group" aria-label={label}>{children}</div>;
}

/** A quiet content region; omit its title when the host already names the pane. */
export function WorkspacePanel({ title, actions, children, className = '', style }: {
  title?: string; actions?: ReactNode; children: ReactNode; className?: string; style?: CSSProperties;
}) {
  const id = useId();
  return <section className={`oaw-panel ${className}`} aria-labelledby={title ? id : undefined} style={style}>
    {(title || actions) && <header className="oaw-panel-heading">{title && <h2 id={id}>{title}</h2>}{actions}</header>}
    <div className="oaw-panel-content">{children}</div>
  </section>;
}

/** Empty and loading content retain a readable explanation and actionable next step. */
export function WorkspaceEmptyState({ title, description, actions, icon, busy = false, className = '' }: {
  title: string; description?: ReactNode; actions?: ReactNode; icon?: ReactNode; busy?: boolean; className?: string;
}) {
  return <div className={`oaw-empty-state ${className}`} role={busy ? 'status' : undefined} aria-busy={busy || undefined}>
    {icon && <span className="oaw-empty-icon" aria-hidden="true">{icon}</span>}
    <strong>{title}</strong>
    {description && <div className="oaw-empty-description">{description}</div>}
    {actions && <div className="oaw-empty-actions">{actions}</div>}
  </div>;
}
