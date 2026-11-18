import React from 'react';
import { formatCount } from '../../lib/format';

// How much of a limit is used: ink while there is room, the warning colour from 80%, negative when it is used up.
export function usageTone(used, limit) {
  if (!limit) return 'var(--chart-ink)';
  const share = used / limit;
  return share >= 1 ? 'var(--pill-negative-ink)' : share >= 0.8 ? 'var(--warning)' : 'var(--chart-ink)';
}

export default function UsageBar({ label, used, limit, note }) {
  const share = limit ? Math.min(1, used / limit) : 0;
  return (
    <div className="min-w-0">
      <p className="flex items-baseline justify-between gap-3 text-body">
        <span className="truncate">{label}</span>
        <span className="num flex-none">
          {formatCount(used)}
          {limit ? <span className="text-muted"> / {formatCount(limit)}</span> : null}
        </span>
      </p>
      {limit ? (
        <div className="mt-1.5 h-1.5 rounded-full bg-raised overflow-hidden" role="meter" aria-label={label}
             aria-valuemin={0} aria-valuemax={limit} aria-valuenow={Math.min(used, limit)}>
          <div className="h-full rounded-full" style={{ width: `${share * 100}%`, background: usageTone(used, limit) }} />
        </div>
      ) : null}
      {note ? <p className="mt-1 text-small text-muted">{note}</p> : null}
    </div>
  );
}
