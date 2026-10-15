import { memo, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { ArrowDown, Check, ChevronRight, CircleAlert, LoaderCircle, Square } from "lucide-react";
import { t, useLocale } from "../i18n";
import { toolFailed, type RunActivityItem } from "../state/runActivity";
import type { ExecutionGroup } from '../state/conversationTranscript';
import type { ConversationHistorySection } from '../state/conversationSections';
import { elapsedMilliseconds, executionElapsed, toolElapsed } from '../state/activityTiming';
import { MarkdownMessage } from "./MarkdownMessage";
import { hasToolData, ToolData } from './ToolData';
import { toolPresentation } from '../state/toolPresentation';

function Duration({ milliseconds }: { milliseconds?: number }) {
  if (milliseconds === undefined) return null;
  const seconds = milliseconds / 1000;
  const label = seconds < 60 ? t('{seconds}s', { seconds: seconds.toFixed(1) })
    : t('{minutes}m {seconds}s', { minutes: Math.floor(seconds / 60), seconds: Math.floor(seconds % 60) });
  return <span className="conversation-duration" title={t('Elapsed time')}>{label}</span>;
}

function useActivityClock(active: boolean) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

const ToolActivity = memo(function ToolActivity({ item, active, now, open, onOpen }: {
  item: RunActivityItem; active: boolean; now: number; open: boolean; onOpen: (id: string, open: boolean) => void;
}) {
  useLocale();
  const failed = toolFailed(item);
  const completed = item.type === "tool_completed";
  const pending = !completed && active;
  const status = failed ? t("Failed") : completed ? t("Completed") : pending ? t("Running") : t("Stopped");
  const Icon = failed ? CircleAlert : completed ? Check : pending ? LoaderCircle : Square;
  const response = item.response;
  const presentation = toolPresentation(item);
  const hasInput = hasToolData(presentation.input);
  const hasOutput = hasToolData(response);
  const hasError = hasToolData(item.error);
  const hasDetails = hasInput || hasOutput || hasError;
  const className = `run-tool${failed ? ' is-error' : ''}`;
  const heading = <>
      <Icon size={12} className={pending ? "run-activity-spinner" : undefined} aria-label={status} />
      <span className="run-tool-name" title={presentation.name}>{presentation.name}</span>
      <Duration milliseconds={toolElapsed(item, active, now)} />
      {hasDetails && <ChevronRight size={12} className="run-tool-chevron" aria-hidden="true" />}
    </>;
  if (!hasDetails) return <div className={className} data-activity-id={item.id}>
    <div className="run-tool-summary">{heading}</div>
  </div>;
  return <details className={className} open={open} data-activity-id={item.id}
    onToggle={event => { if (event.target === event.currentTarget) onOpen(item.id, event.currentTarget.open); }}>
    <summary className="run-tool-summary">{heading}</summary>
    {open ? <div className="run-tool-details">
      {hasInput && <section aria-label={t('Input')}><span>{t('Input')}</span><ToolData value={presentation.input} /></section>}
      {hasOutput && <section aria-label={t('Output')}><span>{t('Output')}</span><ToolData value={response} /></section>}
      {hasError && <section aria-label={t('Error')}><span>{t('Error')}</span><ToolData value={item.error} /></section>}
      {item.truncated ? <small>{t("Details truncated")}</small> : null}
    </div> : null}
  </details>;
});

export const RunActivityText = memo(function RunActivityText({ item }: { item: RunActivityItem }) {
  return <div className={item.type === "agent_progress" ? "run-activity-progress" : "run-activity-text"}>
    {item.type === "agent_message" ? <MarkdownMessage content={item.text ?? ""} /> : <p>{item.text}</p>}
  </div>;
});

export function RunActivityStatus({ waiting = false, stopping = false, onStop }: {
  waiting?: boolean;
  stopping?: boolean;
  onStop?: () => void;
}) {
  useLocale();
  return <div className="run-activity-footer">
      {onStop ? <button type="button" className="run-stop-button" disabled={stopping} onClick={onStop}>
        <Square size={10} aria-hidden="true" />{stopping ? t("Stopping…") : t("Stop")}
      </button> : null}
      <span role="status">
        {!waiting ? <LoaderCircle size={12} className="run-activity-spinner" aria-hidden="true" /> : null}
        {waiting ? t("Waiting") : t("Working")}
      </span>
    </div>;
}

export function ConversationHistory({ section, children }: { section: ConversationHistorySection; children: ReactNode }) {
  useLocale();
  const [open, setOpen] = useState(false);
  return <details className="conversation-history" open={open} data-history-run-id={section.run.run_id}
    onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>
      <span>{t('Work log')}</span>
      <Duration milliseconds={elapsedMilliseconds(section.run.started_at, section.run.finished_at)} />
      <ChevronRight size={12} className="conversation-history-chevron" aria-hidden="true" />
    </summary>
    {open && <div className="conversation-history-content">{children}</div>}
  </details>;
}

/** A stable execution segment, with independent disclosure and scroll intent. */
export function ConversationExecution({ group, agentName, history = false }: { group: ExecutionGroup; agentName: string; history?: boolean }) {
  useLocale();
  const regionId = useId();
  // Explicit user choice applies to the current phase. A live segment closes
  // once sealed, but remains manually inspectable afterward.
  const [choice, setChoice] = useState<{ phase: boolean; open: boolean }>();
  const phase = history || group.expanded;
  const manual = choice?.phase === phase;
  const open = manual ? choice.open : phase;
  const [openedTools, setOpenedTools] = useState<Set<string>>(() => new Set());
  // The bounded viewport is only an automatic live preview. Explicit reading
  // (opening a group or any tool) uses the main transcript's full height.
  const compact = group.running && !history && !manual && openedTools.size === 0;
  const viewport = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [away, setAway] = useState(false);
  const pending = group.running && group.items.some(item => item.type === 'tool_started');
  const now = useActivityClock(pending);
  const failed = group.items.filter(toolFailed).length;
  const stopped = !group.running && group.items.some(item => item.type === 'tool_started');
  const Icon = pending ? LoaderCircle : failed ? CircleAlert : stopped ? Square : Check;
  const label = pending ? t('Running') : failed ? t('Failed') : stopped ? t('Stopped') : t('Completed');
  useLayoutEffect(() => {
    const node = viewport.current;
    if (open && compact && node && follow.current) node.scrollTop = node.scrollHeight;
  }, [open, compact, group.items]);
  return <div className="conversation-execution" data-execution-id={group.id} data-running={pending || undefined}>
    <button type="button" className="conversation-execution-summary" aria-expanded={open} aria-controls={regionId}
      onClick={() => setChoice({ phase, open: !open })}>
      <Icon size={12} className={pending ? 'run-activity-spinner' : undefined} aria-label={label} />
      <span>{group.items.length} {t('tool calls')}</span>
      <Duration milliseconds={executionElapsed(group.items, group.running, now)} />
      {failed > 0 && <span className="conversation-execution-error">{t('{count} failed', { count: failed })}</span>}
      <ChevronRight size={12} className="conversation-execution-chevron" aria-hidden="true" />
    </button>
    {open && <div className="conversation-execution-body">
      <div id={regionId} className={`conversation-execution-scroll${compact ? ' nowheel' : ''}`} data-compact={compact || undefined} ref={viewport} role="region"
        aria-label={t('Execution activity for {name}', { name: agentName })} tabIndex={0}
        onScroll={event => {
          // Nested tool output can scroll independently of the group.
          if (!compact || event.target !== event.currentTarget) return;
          const node = event.currentTarget;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 24;
          setAway(!follow.current);
        }}>
        {group.truncated && <small className="run-activity-note">{t('Earlier activity is no longer in this live view')}</small>}
        {group.items.map(item => <ToolActivity key={item.id} item={item} active={group.running} now={now}
          open={openedTools.has(item.id)} onOpen={(id, expanded) => setOpenedTools(previous => {
            if (previous.has(id) === expanded) return previous;
            const next = new Set(previous);
            if (expanded) next.add(id); else next.delete(id);
            return next;
          })} />)}
      </div>
      {compact && away && group.running && <button type="button" className="conversation-execution-latest" onClick={() => {
        follow.current = true;
        if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
        setAway(false);
      }}><ArrowDown size={11} />{t('Latest activity')}</button>}
    </div>}
  </div>;
}
