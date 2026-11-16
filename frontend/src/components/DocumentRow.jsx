/** One document in the Documents table. The name opens the preview (where the text can be edited); everything else
    is in the row's "⋯" menu: Rename, Undo / Redo the latest edit, Download, Download as PDF, Remove. A choice that
    changes something asks first, in the row itself, one question at a time. */
import React, { useState } from 'react';
import { FileText } from 'lucide-react';
import { API_BASE_URL } from '../services/api';
import { documentFiles } from '../lib/documentFiles';
import { formatCount, formatDay, formatSize } from '../lib/format';
import ActionMenu from './ActionMenu';
import RenameField from './RenameField';

const plural = (n, word) => `${formatCount(n)} ${word}${n === 1 ? '' : 's'}`;

function Status({ doc }) {
  if (doc.status === 'failed') return <span className="pill-negative">Failed</span>;
  if (doc.status !== 'indexed') return <span className="pill-warning">Indexing</span>;
  return <span className="text-body text-muted">Ready</span>;
}

/** What each choice asks before it runs, and what it says while running. */
const QUESTIONS = {
  undo: { question: 'Undo the latest edit?', yes: 'Undo', busy: 'Undoing…' },
  redo: { question: 'Bring the undone edit back?', yes: 'Redo', busy: 'Redoing…' },
  remove: { question: 'Remove from the library?', yes: 'Remove', busy: 'Removing…', danger: true },
};

function Confirm({ kind, onYes, onNo }) {
  const [busy, setBusy] = useState(false);
  const q = QUESTIONS[kind];
  return (
    <span className="inline-flex items-center gap-3 whitespace-nowrap text-body" role="group" aria-label={q.question}>
      <span className="text-muted">{q.question}</span>
      <button type="button" disabled={busy}
              className={`font-medium hover:underline underline-offset-4 disabled:opacity-50 ${q.danger ? 'text-negative' : 'text-ink'}`}
              onClick={async () => {
                setBusy(true);
                try { await onYes(); } catch { /* the page shows what went wrong */ } finally { setBusy(false); onNo(); }
              }}>
        {busy ? q.busy : q.yes}
      </button>
      {!busy && <button type="button" className="text-muted hover:text-ink" onClick={onNo}>Cancel</button>}
    </span>
  );
}

/** onPreview(doc); onRenamed(result); onUndo, onRedo, onRemove: (doc) => promise; onError(message). */
export default function DocumentRow({ doc, onPreview, onRenamed, onUndo, onRedo, onRemove, onError }) {
  const [renaming, setRenaming] = useState(false);
  const [asking, setAsking] = useState(null);   // undo | redo | remove
  const ready = doc.status === 'indexed';
  const files = documentFiles(API_BASE_URL, doc);
  const run = { undo: onUndo, redo: onRedo, remove: onRemove };

  const menu = [
    ready && { label: 'Preview and edit', onSelect: () => onPreview(doc) },
    ready && { label: 'Rename', onSelect: () => { setAsking(null); setRenaming(true); } },
    ready && doc.edit_id != null && { label: 'Undo latest edit', onSelect: () => setAsking('undo') },
    ready && doc.redo_id != null && { label: 'Redo', onSelect: () => setAsking('redo') },
    { label: 'Download', href: files.downloadUrl, download: true, separatorBefore: true },
    ready && doc.ext && doc.ext !== '.pdf' && { label: 'Download as PDF', href: files.pdfUrl, download: true },
    doc.status !== 'processing' && { label: 'Remove', danger: true, separatorBefore: true, onSelect: () => setAsking('remove') },
  ];

  return (
    <tr className="border-t border-rule align-middle">
      <td className="px-4 py-3 max-w-0 w-full md:w-[42%]">
        {renaming ? (
          <RenameField doc={doc} onRenamed={onRenamed} onError={onError} onClose={() => setRenaming(false)} />
        ) : (
          <span className="flex items-center gap-2.5 min-w-0">
            <FileText className="w-4 h-4 flex-none text-muted" strokeWidth={1.6} aria-hidden="true" />
            <span className="min-w-0">
              {/* the library's name for it (the agent renames it), opening the preview; the uploaded name underneath */}
              {ready ? (
                <button type="button" className="block max-w-full truncate font-medium text-left hover:underline underline-offset-4"
                        title={`Open ${doc.title}`} onClick={() => onPreview(doc)}>
                  {doc.title}
                </button>
              ) : (
                <span className="block truncate font-medium" title={doc.title}>{doc.title}</span>
              )}
              {doc.filename !== doc.title && (
                <span className="block truncate text-small text-muted" title="Uploaded as">{doc.filename}</span>
              )}
              {/* Phone: the other fields move to a second line instead of being dropped. */}
              <span className="md:hidden block text-small text-muted num">
                {ready
                  ? `${doc.pages ? `${plural(doc.pages, 'page')} · ` : ''}${plural(doc.chunks, 'passage')} · ${formatSize(doc.size_bytes)}`
                  : formatSize(doc.size_bytes)}
                {!ready && <span className="ml-2 align-middle"><Status doc={doc} /></span>}
              </span>
              {doc.error && <span className="block text-small text-[var(--pill-negative-ink)] truncate" title={doc.error}>{doc.error}</span>}
            </span>
          </span>
        )}
      </td>
      {/* A document still being indexed has no pages or passages yet: a dash, not a zero. */}
      <td className="px-4 text-right num hidden md:table-cell">{ready && doc.pages ? formatCount(doc.pages) : <span className="text-muted">—</span>}</td>
      <td className="px-4 text-right num hidden md:table-cell">{ready ? formatCount(doc.chunks) : <span className="text-muted">—</span>}</td>
      <td className="px-4 text-right num text-muted hidden md:table-cell whitespace-nowrap">{formatSize(doc.size_bytes)}</td>
      <td className="px-4 text-right text-muted hidden md:table-cell whitespace-nowrap">{formatDay(doc.created_at)}</td>
      <td className="px-4 hidden md:table-cell"><Status doc={doc} /></td>
      <td className="px-2 py-2 text-right whitespace-nowrap">
        {asking ? (
          <Confirm kind={asking} onYes={() => run[asking](doc)} onNo={() => setAsking(null)} />
        ) : (
          <ActionMenu label={`Actions for ${doc.title}`} items={menu} />
        )}
      </td>
    </tr>
  );
}
