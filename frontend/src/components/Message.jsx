import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ChevronDown, ChevronRight, Info, Loader2, Wand2 } from 'lucide-react';
import ToolBadge from './ToolBadge';
import SourceCard from './SourceCard';

const NODE_LABELS = {
  router: 'Intake',
  review: 'Large-model review',
  fallback: 'Fallback',
  greeting: 'Greeting',
  direct_answer: 'Direct answer',
  rewrite_query: 'Query rewrite',
  retrieve: 'Retrieve',
  grade_documents: 'Grade chunks',
  generate: 'Generate',
  web_search: 'Web search',
  evaluate: 'Evaluate answer',
  escalate: 'Escalate',
  no_results: 'No results',
  finalize: 'Finalize',
  error: 'Error',
};

function TraceList({ steps, live = false, status = null }) {
  return (
    <ol className="space-y-1.5">
      {steps.map((s, i) => {
        const skipped = s.data?.skipped; // "review skipped" steps are shown quietly
        return (
          <li key={s.id || i} className={`flex items-start space-x-2 text-xs ${skipped ? 'opacity-50' : ''}`}>
            <span className={`mt-1 w-1.5 h-1.5 rounded-full flex-shrink-0 ${s.node === 'review' ? 'bg-violet-400' : 'bg-indigo-500'}`} />
            <span>
              <span className="font-semibold text-slate-300">{NODE_LABELS[s.node] || s.node}</span>
              <span className="text-slate-400"> — {s.detail}</span>
            </span>
          </li>
        );
      })}
      {live && (
        <li className="flex items-center space-x-2 text-xs text-indigo-300">
          <Loader2 className="w-3 h-3 animate-spin" />
          <span>{status || 'Starting…'}</span>
        </li>
      )}
    </ol>
  );
}

function Markdown({ text }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export default function Message({ msg }) {
  const isUser = msg.sender === 'user';
  const [showTrace, setShowTrace] = useState(false);

  return (
    <div className={`flex flex-col ${isUser ? 'items-end' : 'items-start'} space-y-1`}>
      <span className="text-xs font-semibold text-slate-400 px-1">{isUser ? 'You' : 'Agent'}</span>
      <div
        className={`max-w-3xl rounded-2xl px-4 py-3 text-sm shadow-md ${
          isUser
            ? 'bg-indigo-600 text-white rounded-br-none'
            : msg.error
              ? 'bg-rose-950/40 border border-rose-900/50 text-rose-300 rounded-bl-none'
              : 'bg-slate-900 border border-slate-800 text-slate-200 rounded-bl-none'
        }`}
      >
        {!isUser && !msg.pending && msg.notices?.length > 0 && (
          <div className="mb-3 space-y-1.5">
            {msg.notices.map((n, i) => (
              <div
                key={i}
                className="flex items-start space-x-2 text-xs text-sky-300 bg-sky-950/40 border border-sky-900/50 rounded-lg px-3 py-2"
              >
                <Info className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
                <span>{n}</span>
              </div>
            ))}
          </div>
        )}

        {msg.pending ? (
          <>
            <TraceList steps={msg.steps} live status={msg.status} />
            {msg.draft && (
              <div className="mt-3 pt-3 border-t border-slate-800/80">
                <Markdown text={msg.draft} />
                <span className="inline-block w-1.5 h-4 align-middle bg-indigo-400 animate-pulse ml-0.5" />
              </div>
            )}
          </>
        ) : isUser || msg.error ? (
          <p className="whitespace-pre-wrap leading-relaxed">{msg.text}</p>
        ) : (
          <Markdown text={msg.text} />
        )}

        {!isUser && !msg.pending && msg.rewrittenQueries?.length > 0 && (
          <div className="mt-3 text-xs text-slate-400 flex items-start space-x-1.5">
            <Wand2 className="w-3.5 h-3.5 text-violet-400 mt-0.5 flex-shrink-0" />
            <span>
              Query rewritten as:{' '}
              {msg.rewrittenQueries.map((q, i) => (
                <span key={i} className="italic text-slate-300">
                  “{q}”{i < msg.rewrittenQueries.length - 1 ? ', ' : ''}
                </span>
              ))}
            </span>
          </div>
        )}

        {!isUser && !msg.pending && <SourceCard sources={msg.sources} />}

        {!isUser && !msg.pending && msg.tool && (
          <div className="mt-3 pt-2 border-t border-slate-800/80">
            <div className="flex items-center justify-between space-x-3">
              <div className="flex items-center space-x-2">
                <ToolBadge tool={msg.tool} />
                {msg.evaluation && (
                  <span
                    className={`text-[11px] px-2 py-0.5 rounded-full border ${
                      msg.evaluation.sufficient
                        ? 'text-emerald-400 border-emerald-500/20 bg-emerald-500/10'
                        : 'text-amber-400 border-amber-500/20 bg-amber-500/10'
                    }`}
                    title={msg.evaluation.missing || 'Answer evaluated as sufficient'}
                  >
                    Quality {msg.evaluation.score}/5
                  </span>
                )}
                {msg.latencyMs != null && (
                  <span className="text-[11px] text-slate-500">{(msg.latencyMs / 1000).toFixed(1)}s</span>
                )}
              </div>
              {msg.trace?.length > 0 && (
                <button
                  onClick={() => setShowTrace((v) => !v)}
                  className="text-[11px] text-slate-400 hover:text-slate-200 flex items-center space-x-1"
                >
                  {showTrace ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                  <span>Agent steps ({msg.trace.length})</span>
                </button>
              )}
            </div>
            {showTrace && (
              <div className="mt-3 bg-slate-950/60 border border-slate-800 rounded-lg p-3">
                <TraceList steps={msg.trace} />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
