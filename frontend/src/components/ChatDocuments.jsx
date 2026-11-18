/** The chat's side panel: the documents this conversation renamed, edited or undid, newest first, each with
    Preview and Download. Only this chat's: another chat's changes never show here. */
import React from 'react';
import { FileText, Loader2, X } from 'lucide-react';
import { openPreview } from '../state/previewStore';
import DocumentLinks from './DocumentLinks';

const ACTION_LABEL = { renamed: 'Renamed', edited: 'Edited', undone: 'Edit undone', redone: 'Edit redone' };

/** docs, error: from useChatDocuments (state/chatDocuments.js). */
export default function ChatDocuments({ docs, error, onClose }) {
  return (
    <aside aria-label="Documents changed in this chat" className="h-full flex flex-col bg-ground border-l border-line">
      <div className="flex-none flex items-center gap-2 h-14 pl-4 pr-2 border-b border-line">
        <h2 className="flex-1 text-section font-medium">In this chat</h2>
        <button type="button" className="btn-ghost w-8 h-8 px-0" onClick={onClose} aria-label="Close the panel">
          <X className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        {error && <p className="mb-2 text-small text-[var(--pill-negative-ink)]">Couldn't load: {error}</p>}
        {docs === null && !error && (
          <p className="flex items-center gap-2 text-body text-muted"><Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> Loading…</p>
        )}
        {docs?.length === 0 && (
          <p className="text-body text-muted">Documents this chat renames or edits appear here, to preview and download.</p>
        )}
        <ul className="flex flex-col gap-2">
          {docs?.map((d) => (
            <li key={d.id} className="panel px-3 py-2.5">
              <p className="flex items-start gap-2 min-w-0">
                <FileText className="w-4 h-4 mt-0.5 flex-none text-muted" strokeWidth={1.6} aria-hidden="true" />
                <span className="min-w-0">
                  <span className="block font-medium text-body break-words">{d.title}</span>
                  <span className="block text-small text-muted">
                    {ACTION_LABEL[d.action] || d.action} · {(d.ext || '').replace('.', '').toUpperCase()}
                    {d.pages ? ` · ${d.pages} page${d.pages === 1 ? '' : 's'}` : ''}
                  </span>
                </span>
              </p>
              <p className="mt-2 flex flex-wrap items-center gap-3 text-body">
                {d.status === 'indexed'
                  ? <button type="button" className="link-action" onClick={() => openPreview(d)}>Preview</button>
                  : <span className="text-muted">{d.status === 'processing' ? 'Indexing…' : 'Failed'}</span>}
                <DocumentLinks doc={d} />
              </p>
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
