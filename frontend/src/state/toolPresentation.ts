import type { RunActivityItem } from './runActivity';

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined;
}

export function toolPresentation(item: RunActivityItem) {
  const envelope = object(item.arguments);
  const wrapped = item.name === 'oaw_invoke_tool';
  const capability = item.capability_id ?? (wrapped && typeof envelope?.capability_id === 'string' ? envelope.capability_id : undefined);
  // Only documented named contracts can be decoded. Keep opaque IDs intact.
  const named = capability?.match(/^(?:operation|host):(.+)$/)?.[1];
  const name = item.tool_name || (wrapped ? named || capability : undefined) || item.name || 'tool';
  const input = wrapped && envelope ? envelope.arguments : item.arguments;
  return { name, input };
}
