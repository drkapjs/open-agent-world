// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, expect, it } from 'vitest';
import { ToolData } from './ToolData';
import { useLocale } from '../i18n';

afterEach(cleanup);
it('shows all nested fields and array items in one table with parent row spans', () => {
  useLocale.setState({ locale: 'en' });
  const value = { summary: { goal: 'Review', approach: 'Inspect evidence',
    key_decisions: Array.from({ length: 25 }, (_, i) => `entry-${i}`) },
    success: false, exit_code: 0, result: null, empty: '',
    records: [{ metrics: { score: 1 } }, { metrics: { score: 2 } }] };
  const view = render(<ToolData value={JSON.stringify(value)} />);
  expect(screen.getByText('false')).toBeTruthy();
  expect(screen.getByText('0')).toBeTruthy();
  expect(screen.getByText('null')).toBeTruthy();
  expect(screen.getByText('Empty text')).toBeTruthy();
  expect(screen.getByText('entry-0')).toBeTruthy();
  expect(screen.getByText('entry-24')).toBeTruthy();
  expect(screen.getAllByRole('table')).toHaveLength(1);
  expect(view.container.querySelector('details, ol, button')).toBeNull();
  expect(screen.getByText('summary').getAttribute('rowspan')).toBe('27');
  expect(screen.getByText('key decisions').getAttribute('rowspan')).toBe('25');
  expect(screen.getByText('goal').getAttribute('colspan')).toBe('3');
  expect(screen.getAllByText('metrics')).toHaveLength(2);
  const last = screen.getByText('entry-24').closest('td')!;
  expect(last.dataset.path).toBe('summary / key decisions / 25');
  expect(last.getAttribute('headers')?.split(' ')).toHaveLength(3);
  expect(new Set([...view.container.querySelectorAll('th')].map(node => node.id)).size).toBe(view.container.querySelectorAll('th').length);
  expect(screen.queryByText('Raw JSON')).toBeNull();
});

it('renders untrusted text literally and leaves incomplete JSON readable', () => {
  const view = render(<ToolData value={'{"html":"<script>alert(1)</script>"'} />);
  expect(view.container.querySelector('script')).toBeNull();
  expect(view.container.textContent).toContain('<script>alert(1)</script>');
});
