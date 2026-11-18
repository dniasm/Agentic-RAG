import React, { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import { ChevronDown, ChevronRight, Info, Loader2 } from 'lucide-react';
import { formatSeconds } from '../lib/format';
import { withCitations, withMath } from '../lib/answerText';
import DocumentActions from './DocumentActions';

const STEP_LABELS = {
  router: 'Read the question',
  review: 'Second opinion',
  fallback: 'Next source',
  greeting: 'Replied',
  direct_answer: 'Model knowledge',
  rewrite_query: 'Reworded the search',
  retrieve: 'Searched documents',
  grade_documents: 'Ranked passages',
  generate: 'Wrote the answer',
  web_search: 'Searched the web',
  evaluate: 'Checked the answer',
  no_results: 'No answer found',
  finalize: 'Finished',
  error: 'Error',
};

const remarkPlugins = [remarkGfm, [remarkMath, { singleDollarTextMath: false }]];
const rehypePlugins = [[rehypeKatex, { throwOnError: false, strict: false }]];

function sourceTitle(src) {
  if (!src) return undefined;
  if (src.type === 'web') return src.title || src.url;
  const place = docPlace(src);
  return `${src.document}${place ? `, ${place}` : ''}`;
}

// Where in a document a passage is: its page, and whether the page was a scan read by OCR.
function docPlace(src) {
  const page = src.page != null ? `page ${src.page}` : '';
  return src.ocr ? `${page}${page ? ' · ' : ''}scanned, read by OCR` : page;
}

function Markdown({ text, sources = [] }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={{
          a: ({ node, href = '', children, ...props }) => {
            if (href.startsWith('#cite-')) {
              const src = sources[Number(href.slice(6)) - 1];
              return (
                <a href={src?.url || href} className="cite-mark" title={sourceTitle(src)}
                   target={src?.url ? '_blank' : undefined} rel="noopener noreferrer"
                   onClick={src?.url ? undefined : (e) => e.preventDefault()}>
                  {children}
                </a>
              );
            }
            return <a href={href} {...props} target="_blank" rel="noopener noreferrer">{children}</a>;
          },
        }}
      >
        {withCitations(withMath(text || ''))}
      </ReactMarkdown>
    </div>
  );
}

function hostname(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return url; }
}

/** "CCS_using_SVM.pdf, p. 12 and p. 14 · nlp.pdf, p. 3" — documents in citation order, their pages listed. */
function documentSummary(sources) {
  const byDoc = new Map();
  for (const s of sources) {
    const pages = byDoc.get(s.document) || [];
    if (s.page != null && !pages.includes(s.page)) pages.push(s.page);
    byDoc.set(s.document, pages);
  }
  const docs = [...byDoc].map(([doc, pages]) => {
    if (!pages.length) return doc;
    const sorted = [...pages].sort((a, b) => a - b).map((p) => `p. ${p}`);
    return `${doc}, ${sorted.length > 1 ? `${sorted.slice(0, -1).join(', ')} and ${sorted.at(-1)}` : sorted[0]}`;
  });
  return docs.length > 2 ? `${docs.slice(0, 2).join(' · ')} · ${docs.length - 2} more` : docs.join(' · ');
}

function Provenance({ msg }) {
  const sources = msg.sources || [];
  if (msg.tool === 'rag') {
    return (
      <>
        <span className="font-medium text-hue">From your documents</span>
        {sources.length > 0 && <span className="min-w-0">{documentSummary(sources)}</span>}
      </>
    );
  }
  if (msg.tool === 'web_search') {
    const sites = [...new Set(sources.map((s) => hostname(s.url)))];
    return (
      <>
        <span className="font-medium text-hue">From a web search</span>
        {sites.length > 0 && <span>{sites.slice(0, 3).join(', ')}{sites.length > 3 ? ` and ${sites.length - 3} more` : ''}</span>}
      </>
    );
  }
  if (msg.tool === 'direct') return <span className="font-medium text-ink">From the model's own knowledge</span>;
  if (msg.tool === 'no_results') return <span className="font-medium text-ink">No source had an answer</span>;
  return null;
}

function Check({ evaluation }) {
  if (!evaluation) return null;
  if (evaluation.verified === false) return <span title={evaluation.error}>Not checked</span>;
  if (!evaluation.sufficient) {
    return <span className="text-warning" title={evaluation.missing || undefined}>Partly answered</span>;
  }
  return <span>Checked <span className="num">{evaluation.score}/5</span></span>;
}

function Steps({ steps }) {
  return (
    <ol className="flex flex-col gap-2">
      {steps.map((s, i) => (
        <li key={s.id || i} className={`text-body ${s.data?.skipped ? 'opacity-60' : ''}`}>
          <span className="font-medium">{STEP_LABELS[s.node] || s.node}</span>
          <span className="text-muted">: {s.detail}</span>
        </li>
      ))}
    </ol>
  );
}

function Sources({ sources }) {
  return (
    <ol className="flex flex-col">
      {sources.map((src, i) => (
        <li key={i} className="flex gap-3 py-2.5 border-t border-rule first:border-t-0">
          <span className="cite-mark w-6 flex-none pt-px">[{i + 1}]</span>
          <div className="min-w-0 flex-1">
            <p className="text-body flex flex-wrap gap-x-2">
              {src.type === 'web' ? (
                <a href={src.url} target="_blank" rel="noopener noreferrer" className="font-medium truncate hover:underline underline-offset-4">
                  {src.title || hostname(src.url)}
                </a>
              ) : (
                <span className="font-medium truncate">{src.document}</span>
              )}
              <span className="text-muted">{src.type === 'web' ? hostname(src.url) : docPlace(src)}</span>
            </p>
            {src.snippet && <p className="mt-0.5 text-body text-muted line-clamp-2">{src.snippet}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function Disclosure({ label, count, open, onToggle }) {
  const Icon = open ? ChevronDown : ChevronRight;
  return (
    <button type="button" onClick={onToggle} aria-expanded={open}
            className="tap-target inline-flex items-center gap-0.5 font-medium text-ink hover:underline underline-offset-4">
      {label} <span className="num">({count})</span>
      <Icon className="w-3.5 h-3.5" aria-hidden="true" />
    </button>
  );
}

function AgentAnswer({ msg, undoable, redoable, onUndo, onRedo }) {
  const [open, setOpen] = useState(null); // 'sources' | 'steps' | null
  const toggle = (what) => setOpen((v) => (v === what ? null : what));
  const sources = msg.sources || [];
  const steps = msg.trace || [];

  return (
    <article className="flex flex-col">
      {msg.notices?.length > 0 && (
        <div className="mb-3 flex flex-col gap-1.5">
          {msg.notices.map((n, i) => (
            <p key={i} className="flex gap-2 rounded-control bg-raised px-3 py-2 text-body text-[color:var(--text-2)]">
              <Info className="w-4 h-4 mt-0.5 flex-none text-muted" strokeWidth={1.6} aria-hidden="true" />
              {n}
            </p>
          ))}
        </div>
      )}

      <Markdown text={msg.text} sources={sources} />
      {msg.actions?.length > 0 && (
        <DocumentActions actions={msg.actions} undoable={undoable} redoable={redoable} onUndo={onUndo} onRedo={onRedo} />
      )}

      <div className="mt-3 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-small text-muted">
        <Provenance msg={msg} />
        {msg.evaluation && <><span aria-hidden="true">·</span><Check evaluation={msg.evaluation} /></>}
        {msg.latencyMs != null && <><span aria-hidden="true">·</span><span className="num">{formatSeconds(msg.latencyMs)}</span></>}
        {(sources.length > 0 || steps.length > 0) && <span className="flex-1" />}
        {sources.length > 0 && (
          <Disclosure label="Sources" count={sources.length} open={open === 'sources'} onToggle={() => toggle('sources')} />
        )}
        {steps.length > 0 && (
          <Disclosure label="Steps" count={steps.length} open={open === 'steps'} onToggle={() => toggle('steps')} />
        )}
      </div>

      {open === 'sources' && <div className="mt-3 panel px-4 py-1.5"><Sources sources={sources} /></div>}
      {open === 'steps' && (
        <div className="mt-3 panel px-4 py-3">
          {msg.rewrittenQueries?.length > 0 && (
            <p className="mb-3 text-body">
              <span className="font-medium">Searched for</span>{' '}
              <span className="text-muted">{msg.rewrittenQueries.map((q) => `“${q}”`).join(', ')}</span>
            </p>
          )}
          <Steps steps={steps} />
        </div>
      )}
    </article>
  );
}

function PendingAnswer({ msg }) {
  const recent = msg.steps.slice(-3);
  return (
    <article aria-live="polite" aria-busy="true">
      <p className="flex items-center gap-2 text-body text-muted">
        <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
        {msg.status || 'Reading your question…'}
      </p>
      {recent.length > 0 && !msg.draft && (
        <ul className="mt-2 ml-6 flex flex-col gap-1 text-small text-muted">
          {recent.map((s, i) => (
            <li key={s.id || i} className="line-clamp-1">{STEP_LABELS[s.node] || s.node}: {s.detail}</li>
          ))}
        </ul>
      )}
      {msg.draft && (
        <div className="mt-3">
          <Markdown text={msg.draft} />
          <span className="inline-block w-2 h-4 align-[-2px] bg-ink" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}

export default function Message({ msg, undoable = new Set(), redoable = new Set(), onUndo, onRedo }) {
  if (msg.sender === 'user') {
    return (
      <div className="flex justify-end">
        <p className="bg-raised rounded-card px-3.5 py-2.5 text-reading whitespace-pre-wrap max-w-[85%]">{msg.text}</p>
      </div>
    );
  }
  if (msg.pending) return <PendingAnswer msg={msg} />;
  if (msg.error) {
    return (
      <p className="rounded-control px-3 py-2.5 text-body bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]" role="alert">
        {msg.text}
      </p>
    );
  }
  return <AgentAnswer msg={msg} undoable={undoable} redoable={redoable} onUndo={onUndo} onRedo={onRedo} />;
}
