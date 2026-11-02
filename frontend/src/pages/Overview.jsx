import React, { useEffect, useState } from 'react';
import { ArrowRight } from 'lucide-react';
import { apiService } from '../services/api';
import { formatCount, formatSeconds, formatShare } from '../lib/format';
import { hrefFor } from '../lib/route';

// One hue: answers from the user's own documents are the main data element. Everything else is ink and greys.
const SOURCES = [
  { tool: 'rag', label: 'Your documents', color: 'var(--hue)' },
  { tool: 'direct', label: "Model's own knowledge", color: 'var(--chart-ink)' },
  { tool: 'web_search', label: 'Web search', color: 'var(--chart-2)' },
  { tool: 'greeting', label: 'Small talk', color: 'var(--chart-3)' },
  { tool: 'no_results', label: 'No source had an answer', color: 'var(--chart-4)' },
];

function Kpi({ label, value, sub, loading }) {
  return (
    <div className="px-5 py-4 min-w-0">
      <p className="text-body text-muted">{label}</p>
      {loading
        ? <span className="skeleton block h-[30px] w-16 mt-1.5" />
        : <p className="mt-1.5 text-kpi font-medium num tracking-[-0.01em]">{value}</p>}
      <p className="mt-1 text-small text-muted num min-h-4">{loading ? '' : sub}</p>
    </div>
  );
}

function SourceShare({ stats }) {
  const total = stats.queries;
  const known = new Set(SOURCES.map((s) => s.tool));
  const rows = SOURCES.map((s) => ({ ...s, n: stats.by_tool[s.tool] || 0 }));
  const other = Object.entries(stats.by_tool).filter(([t]) => !known.has(t)).reduce((sum, [, n]) => sum + n, 0);
  if (other) rows.push({ tool: 'other', label: 'Other', color: 'var(--chart-5)', n: other });
  console.assert(rows.reduce((s, r) => s + r.n, 0) === total, 'source counts must add up to the answer total');

  return (
    <section className="panel p-5 lg:col-span-8">
      <h2 className="text-section font-medium">Where answers came from</h2>
      <p className="text-body text-muted">Each answer is written from one source; this is each source's share.</p>

      {total === 0 ? (
        <>
          <div className="mt-5 h-6 rounded bg-raised" aria-hidden="true" />
          <p className="mt-4 text-body text-muted">
            No answers yet. Ask something in{' '}
            <a href={hrefFor('chat')} className="font-medium text-ink underline-offset-4 hover:underline">Chat</a>{' '}
            and its source shows here.
          </p>
        </>
      ) : (
        <>
          <div className="mt-5 flex h-6 gap-[2px] rounded overflow-hidden" role="img"
               aria-label={rows.filter((r) => r.n).map((r) => `${r.label} ${formatShare(r.n, total)}`).join(', ')}>
            {rows.filter((r) => r.n).map((r) => (
              <div key={r.tool} style={{ flexGrow: r.n, flexBasis: 0, background: r.color }} title={`${r.label}: ${r.n}`} />
            ))}
          </div>

          <table className="mt-4 w-full text-body">
            <thead>
              <tr className="text-small text-muted">
                <th className="text-left font-normal h-9">Source</th>
                <th className="text-right font-normal">Answers</th>
                <th className="text-right font-normal w-24">Share</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.tool} className="border-t border-rule">
                  <td className="py-2.5">
                    <span className="flex items-center gap-2.5">
                      <span className="w-2.5 h-2.5 rounded-sm flex-none" style={{ background: r.color }} aria-hidden="true" />
                      <span className={r.tool === 'rag' ? 'font-medium' : ''}>{r.label}</span>
                    </span>
                  </td>
                  <td className={`text-right num ${r.n ? '' : 'text-muted'}`}>{formatCount(r.n)}</td>
                  <td className={`text-right num ${r.n ? '' : 'text-muted'}`}>{formatShare(r.n, total)}</td>
                </tr>
              ))}
              <tr className="border-t border-line">
                <td className="py-2.5 font-medium">Total</td>
                <td className="text-right num font-medium">{formatCount(total)}</td>
                <td className="text-right num text-muted">100%</td>
              </tr>
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Library({ documents }) {
  const ready = (documents || []).filter((d) => d.status === 'indexed').sort((a, b) => b.chunks - a.chunks);
  const top = ready.slice(0, 6);
  const max = top[0]?.chunks || 1;
  const passages = ready.reduce((s, d) => s + d.chunks, 0);

  return (
    <section className="panel p-5 lg:col-span-4 flex flex-col">
      <h2 className="text-section font-medium">Documents</h2>
      <p className="text-body text-muted num">
        {documents == null ? 'Loading…' : `${ready.length} ready · ${formatCount(passages)} passages`}
      </p>

      {documents != null && ready.length === 0 && (
        <p className="mt-5 rounded-control border border-dashed border-control px-4 py-6 text-body text-muted text-center">
          No documents yet, so every answer comes from the web or the model.
        </p>
      )}

      {top.length > 0 && (
        <ol className="mt-4 flex flex-col">
          {top.map((d, i) => (
            <li key={d.id} className="py-2.5 border-t border-rule first:border-t-0">
              <p className="flex items-baseline justify-between gap-3 text-body">
                <span className="truncate" title={d.filename}>{d.filename}</span>
                <span className="num text-muted flex-none">{formatCount(d.chunks)}</span>
              </p>
              <div className="mt-1.5 h-[2px] rounded-full" aria-hidden="true"
                   style={{ width: `${(d.chunks / max) * 100}%`, background: i === 0 ? 'var(--text)' : 'var(--chart-3)' }} />
            </li>
          ))}
        </ol>
      )}
      {/* The card's foot: the one action, pinned so the card can match the height of its neighbour. */}
      <div className="mt-auto pt-4 flex items-baseline justify-between gap-3">
        <a href={hrefFor('documents')} className="link-action">
          Manage documents <ArrowRight className="w-3.5 h-3.5" aria-hidden="true" />
        </a>
        {ready.length > top.length && <span className="text-small text-muted">{ready.length - top.length} more</span>}
      </div>
    </section>
  );
}

export default function Overview({ documents }) {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  const load = () => {
    setError(null);
    apiService.getStats().then(setStats).catch((e) => setError(e.message));
  };
  useEffect(load, []);

  const loading = !stats && !error;
  const s = stats || { queries: 0, by_tool: {} };
  const fromDocs = s.by_tool?.rag || 0;

  const finding = !stats
    ? ''
    : s.queries === 0
      ? 'No answers yet. Ask a question in Chat and this page fills in.'
      : `${formatShare(fromDocs, s.queries)} of your ${formatCount(s.queries)} answers came from your documents`
        + (s.answers_evaluated
          ? `, and ${formatCount(s.answers_sufficient)} of ${formatCount(s.answers_evaluated)} checked answers were judged complete.`
          : '.');

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[1400px] px-4 sm:px-6 lg:px-8 py-8">
        <h1 className="text-title font-medium tracking-[-0.01em]">Overview</h1>
        <p className="mt-1 text-body text-muted min-h-5">{finding}</p>

        {error ? (
          <p className="mt-6 panel px-4 py-3 text-body text-[var(--pill-negative-ink)]">
            Couldn't load your usage: {error}{' '}
            <button className="font-medium underline underline-offset-4" onClick={load}>Retry</button>
          </p>
        ) : (
          <>
            <div className="mt-6 panel grid grid-cols-2 md:grid-cols-5 divide-rule [&>*]:border-rule md:divide-x [&>*:nth-child(n+3)]:border-t md:[&>*:nth-child(n+3)]:border-t-0 [&>*:last-child]:col-span-2 md:[&>*:last-child]:col-span-1">
              <Kpi loading={loading} label="Answers" value={formatCount(s.queries)}
                   sub={`in ${formatCount(s.conversations)} chat${s.conversations === 1 ? '' : 's'}`} />
              <Kpi loading={loading} label="From your documents" value={formatCount(fromDocs)}
                   sub={s.queries ? `${formatShare(fromDocs, s.queries)} of answers` : '—'} />
              <Kpi loading={loading} label="Judged complete"
                   value={s.answers_evaluated ? formatShare(s.answers_sufficient, s.answers_evaluated) : '—'}
                   sub={s.answers_evaluated ? `${formatCount(s.answers_sufficient)} of ${formatCount(s.answers_evaluated)} checked` : 'none checked yet'} />
              <Kpi loading={loading} label="Average answer time" value={formatSeconds(s.avg_latency_ms)}
                   sub="question to finished answer" />
              <Kpi loading={loading} label="Questions reworded" value={formatCount(s.queries_rewritten)}
                   sub={s.queries ? `${formatShare(s.queries_rewritten || 0, s.queries)} of answers` : '—'} />
            </div>

            <div className="mt-3 grid grid-cols-1 lg:grid-cols-12 gap-3">
              {loading
                ? <section className="panel p-5 lg:col-span-8 h-[300px]"><span className="skeleton block h-6 w-full mt-14" /></section>
                : <SourceShare stats={s} />}
              <Library documents={documents} />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
