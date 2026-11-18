import React, { useCallback, useEffect, useState } from 'react';
import KeyCard from '../components/admin/KeyCard';
import GeminiCard from '../components/admin/GeminiCard';
import AccountsTable from '../components/admin/AccountsTable';
import { apiService } from '../services/api';
import { formatCount } from '../lib/format';

const REFRESH_MS = 30000;

// Refreshes every 30 s while the page is visible (not in a background tab).
function useUsage() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => {
    apiService.adminUsage().then((d) => { setData(d); setError(null); }).catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    let timer;
    const tick = () => {
      clearTimeout(timer);
      if (document.visibilityState === 'visible') load();
      timer = setTimeout(tick, REFRESH_MS);
    };
    tick();
    document.addEventListener('visibilitychange', tick);
    return () => { clearTimeout(timer); document.removeEventListener('visibilitychange', tick); };
  }, [load]);
  return { data, error, load };
}

function Voyage({ v }) {
  const uses = Object.entries(v.last_24h);
  return (
    <section className="panel p-5 flex flex-col gap-3 min-w-0">
      <div>
        <h3 className="text-section font-medium">Voyage (reranking and memory)</h3>
        <p className="text-body text-muted num">Limited to {formatCount(v.per_minute)} requests a minute · {formatCount(v.today)} today (UTC)</p>
      </div>
      {uses.length === 0 && <p className="text-body text-muted">No requests in the last 24 hours.</p>}
      {uses.map(([use, n]) => (
        <p key={use} className="flex items-baseline justify-between text-body border-t border-rule pt-2">
          <span className="capitalize">{use}</span><span className="num">{formatCount(n)} in 24 h</span>
        </p>
      ))}
    </section>
  );
}

export default function Admin() {
  const { data, error, load } = useUsage();
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] px-4 sm:px-6 lg:px-8 py-8">
        <h1 className="text-title font-medium tracking-[-0.01em]">Admin</h1>
        <p className="mt-1 text-body text-muted">
          How much each API key is used. Updates every 30 seconds while this page is open.
        </p>
        {error && (
          <p className="mt-6 panel px-4 py-3 text-body text-[var(--pill-negative-ink)]">
            Couldn't load the usage: {error}{' '}
            <button className="font-medium underline underline-offset-4" onClick={load}>Retry</button>
          </p>
        )}
        {!data && !error && <p className="mt-6 text-body text-muted">Loading…</p>}
        {data && (
          <div className="mt-6 flex flex-col gap-4">
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {data.groq.keys.map((k) => <KeyCard key={k.key} entry={k} />)}
            </div>
            <div className="grid gap-4 md:grid-cols-2">
              <GeminiCard g={data.gemini} />
              <Voyage v={data.voyage} />
            </div>
            <AccountsTable accounts={data.accounts} />
          </div>
        )}
      </div>
    </div>
  );
}
