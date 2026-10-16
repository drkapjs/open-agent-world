import { useId, useMemo } from 'react';
import { t } from '../i18n';

export function structuredValue(value: unknown): unknown {
  if (typeof value === 'string' && /^[\s]*[\[{]/.test(value)) {
    try { return JSON.parse(value); } catch { /* Truncated JSON remains readable text. */ }
  }
  return value;
}

export function hasToolData(value: unknown): boolean {
  const parsed = structuredValue(value);
  if (parsed == null) return false;
  if (typeof parsed === 'string') return parsed.trim().length > 0;
  if (typeof parsed === 'object') return Object.keys(parsed).length > 0;
  return true;
}

function Value({ value }: { value: unknown }) {
  if (value !== null && typeof value === 'object') return <span className="tool-data-empty">{Array.isArray(value) ? '[]' : '{}'}</span>;
  if (typeof value === 'string') return value ? <pre className="tool-data-text">{value}</pre>
    : <span className="tool-data-empty">{t('Empty text')}</span>;
  return <code className="tool-data-scalar">{value === null ? 'null' : String(value)}</code>;
}

type Field = { id: number; label: string; start: number; span: number };
type Row = { path: Field[]; value: unknown };

/** A field owns exactly its descendant leaf rows, independent of its label. */
function hierarchy(value: unknown) {
  const rows: Row[] = [];
  let nextId = 0;
  function visit(item: unknown, path: Field[]) {
    const entries = item !== null && typeof item === 'object' ? Object.entries(item) : [];
    if (!entries.length) {
      rows.push({ path, value: item });
      return;
    }
    for (const [key, child] of entries) {
      const field: Field = { id: nextId++, label: Array.isArray(item) ? String(Number(key) + 1) : key,
        start: rows.length, span: 0 };
      visit(child, [...path, field]);
      field.span = rows.length - field.start;
    }
  }
  visit(value, []);
  return { rows, depth: rows.reduce((max, row) => Math.max(max, row.path.length), 0) };
}

/** One continuous table: hierarchy columns on the left, values on the right. */
export function ToolData({ value }: { value: unknown }) {
  const id = useId();
  const { rows, depth } = useMemo(() => hierarchy(structuredValue(value)), [value]);
  const fieldWidth = Math.min(22, 52 / depth);
  return <div className="tool-data">
    {depth ? <table className="tool-data-table">
      <colgroup>{Array.from({ length: depth }, (_, index) => <col key={index} style={{ width: `${fieldWidth}%` }} />)}
        <col style={{ width: `${100 - fieldWidth * depth}%` }} /></colgroup>
      <tbody>{rows.map((row, index) => <tr key={index}>
        {row.path.map((field, level) => field.start === index && <th key={field.id} id={`${id}-${field.id}`}
          className="tool-data-key-cell" rowSpan={field.span} colSpan={level === row.path.length - 1 ? depth - level : 1}
          scope={field.span === 1 ? 'row' : undefined} title={field.label}>
          {field.label.replaceAll('_', ' ')}
        </th>)}
        <td headers={row.path.map(field => `${id}-${field.id}`).join(' ')}
          data-path={row.path.map(field => field.label.replaceAll('_', ' ')).join(' / ')}><Value value={row.value} /></td>
      </tr>)}</tbody>
    </table> : <Value value={rows[0].value} />}
  </div>;
}
