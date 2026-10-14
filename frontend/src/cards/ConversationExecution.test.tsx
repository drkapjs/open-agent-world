// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useLocale } from '../i18n';
import type { ExecutionGroup } from '../state/conversationTranscript';
import { ConversationExecution, RunActivityStatus } from './ConversationActivity';

const group: ExecutionGroup = { kind: 'execution', id: 'run:read', timestamp: '', running: true, expanded: true, truncated: false,
  run: { run_id: 'run', agent_id: 'atlas', status: 'running', tool_count: 1, tool_trace: [] },
  items: [{ id: 'read', type: 'tool_started', name: 'read_file', arguments: { path: 'notes' } }] };
afterEach(cleanup);

describe('Conversation execution segments', () => {
  it('keeps one expandable group, updates tools in place, then folds when archived', () => {
    useLocale.setState({ locale: 'en' });
    const view = render(<ConversationExecution group={group} agentName="Atlas" />);
    const toggle = screen.getByRole('button', { name: /tool calls/ });
    expect(toggle.textContent).not.toContain('Atlas');
    expect(toggle.textContent).not.toContain('Execution details');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    const tool = view.container.querySelector('.run-tool') as HTMLDetailsElement;
    expect(tool.querySelector('pre')).toBeNull();
    act(() => { tool.open = true; fireEvent(tool, new Event('toggle', { bubbles: true })); });
    const done: ExecutionGroup = { ...group, items: [{ ...group.items[0], type: 'tool_completed', response: 'File contents' }] };
    view.rerender(<ConversationExecution group={done} agentName="Atlas" />);
    expect(tool.open).toBe(true);
    expect(within(tool).getByText('File contents')).toBeTruthy();
    view.rerender(<ConversationExecution group={{ ...done, expanded: false }} agentName="Atlas" />);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByRole('region')).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByRole('region')).toBeTruthy();
    view.rerender(<ConversationExecution group={{ ...done, expanded: false, running: false }} agentName="Atlas" />);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
  });

  it('respects manual collapse while tools stream and exposes failure counts while folded', () => {
    const view = render(<ConversationExecution group={group} agentName="Atlas" />);
    const toggle = screen.getByRole('button', { name: /tool calls/ });
    fireEvent.click(toggle);
    view.rerender(<ConversationExecution group={{ ...group, items: [...group.items,
      { id: 'error', type: 'tool_completed', name: 'command', response: { exit_code: 1 } }] }} agentName="Atlas" />);
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByText('1 failed')).toBeTruthy();
  });

  it('follows the inner tail until the reader scrolls up and resumes on explicit request', () => {
    const view = render(<ConversationExecution group={group} agentName="Atlas" />);
    const region = screen.getByRole('region');
    Object.defineProperties(region, { scrollHeight: { configurable: true, value: 600 }, clientHeight: { value: 168 } });
    view.rerender(<ConversationExecution group={{ ...group, items: [...group.items] }} agentName="Atlas" />);
    expect(region.scrollTop).toBe(600);
    region.scrollTop = 20;
    fireEvent.scroll(region);
    view.rerender(<ConversationExecution group={{ ...group, items: [...group.items] }} agentName="Atlas" />);
    expect(region.scrollTop).toBe(20);
    fireEvent.click(screen.getByRole('button', { name: 'Latest activity' }));
    expect(region.scrollTop).toBe(600);
  });

  it('does not portray an interrupted tool as completed and labels truncated history', () => {
    render(<ConversationExecution group={{ ...group, running: false, expanded: false, truncated: true }} agentName="Atlas" />);
    expect(screen.getByLabelText('Stopped')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /tool calls/ }));
    expect(screen.getByText('Earlier activity is no longer in this live view')).toBeTruthy();
  });

  it('retains one stop control per active Run independently of execution groups', () => {
    const stop = vi.fn();
    render(<RunActivityStatus onStop={stop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
    expect(stop).toHaveBeenCalledOnce();
  });
});
