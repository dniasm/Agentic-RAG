// src/pages/Dashboard.jsx
import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { apiService } from '../services/api';

const TOOL_META = {
  rag: { label: 'RAG', color: 'bg-indigo-500' },
  web_search: { label: 'Web Search', color: 'bg-amber-500' },
  direct: { label: 'Direct LLM', color: 'bg-emerald-500' },
  greeting: { label: 'Greeting', color: 'bg-blue-500' },
  no_results: { label: 'No Matches', color: 'bg-rose-500' },
};

function Stat({ label, value, accent = 'text-white', hint }) {
  return (
    <div className="bg-slate-900 p-4 border border-slate-800 rounded-xl">
      <p className="text-xs text-slate-400">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${accent}`}>{value}</p>
      {hint && <p className="text-[11px] text-slate-500 mt-1">{hint}</p>}
    </div>
  );
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    apiService.getStats().then(setStats).catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="flex-1 p-8 text-rose-400 text-sm">{error}</div>;
  if (!stats) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <Loader2 className="w-6 h-6 text-indigo-400 animate-spin" />
      </div>
    );
  }

  const pct = (n, d) => (d ? `${Math.round((n / d) * 100)}%` : '—');
  const ragAnswered = stats.by_tool.rag || 0;
  const ragAttempted = ragAnswered + (stats.by_tool.no_results || 0);
  const toolRows = Object.entries(stats.by_tool).sort((a, b) => b[1] - a[1]);

  return (
    <div className="flex-1 p-8 text-slate-200 overflow-y-auto">
      <h2 className="text-2xl font-bold mb-4">Analytics & Stats</h2>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat label="Total Queries" value={stats.queries} hint={`${stats.conversations} conversations`} />
        <Stat label="RAG Hit Rate" value={pct(ragAnswered, ragAttempted)} accent="text-indigo-400"
              hint="Document questions answered from your files" />
        <Stat label="Active Documents" value={stats.documents} accent="text-emerald-400" hint={`${stats.chunks} vector chunks`} />
        <Stat label="Avg. Latency" value={stats.avg_latency_ms ? `${(stats.avg_latency_ms / 1000).toFixed(1)}s` : '—'} />
        <Stat label="Queries Rewritten" value={stats.queries_rewritten} accent="text-violet-400"
              hint="Rewritten / expanded by the agent" />
        <Stat label="Answers Judged Sufficient" value={pct(stats.answers_sufficient, stats.answers_evaluated)}
              accent="text-emerald-400" hint={`${stats.answers_evaluated} evaluated`} />
      </div>

      <div className="mt-8 bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h3 className="text-sm font-semibold text-white mb-4">Routing breakdown</h3>
        {toolRows.length === 0 && <p className="text-sm text-slate-500">No queries yet.</p>}
        <div className="space-y-3">
          {toolRows.map(([tool, n]) => {
            const meta = TOOL_META[tool] || { label: tool, color: 'bg-slate-500' };
            return (
              <div key={tool}>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-300">{meta.label}</span>
                  <span className="text-slate-400">{n} · {pct(n, stats.queries)}</span>
                </div>
                <div className="h-2 bg-slate-800 rounded-full overflow-hidden">
                  <div className={`h-full ${meta.color}`} style={{ width: `${(n / stats.queries) * 100}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
