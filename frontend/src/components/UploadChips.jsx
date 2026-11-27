import React from 'react';
import { AlertCircle, Check, FileText, Loader2, X } from 'lucide-react';
import { removeUpload } from '../state/chatUploads';

const LABEL = { uploading: 'Uploading', indexing: 'Indexing', ready: 'Ready', failed: 'Failed' };

/** One chip per file added from the chat box. A file still being uploaded or indexed can't be removed. */
export default function UploadChips({ chips }) {
  if (!chips.length) return null;
  return (
    <ul className="flex flex-wrap gap-2 px-3 pt-3" aria-label="Files added to this chat">
      {chips.map((c) => {
        const busy = c.status === 'uploading' || c.status === 'indexing';
        const failed = c.status === 'failed';
        const tone = failed ? 'bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]' : 'bg-raised text-[color:var(--text-2)]';
        const Icon = busy ? Loader2 : failed ? AlertCircle : c.status === 'ready' ? Check : FileText;
        return (
          <li key={c.cid} className={`flex items-start gap-1.5 max-w-full rounded-control px-2 py-1.5 text-small ${tone}`}
              role={failed ? 'alert' : 'status'}>
            <Icon className={`w-3.5 h-3.5 mt-0.5 flex-none ${busy ? 'animate-spin' : ''}`} aria-hidden="true" />
            <span className="min-w-0 break-words">
              <span className="font-medium" title={c.name}>{failed ? '' : c.name}</span>
              {failed ? `${c.name}: ${c.detail}`.replace(`${c.name}: ${c.name}: `, `${c.name}: `) : <span className="opacity-80"> · {c.status === 'indexing' && c.detail ? c.detail : LABEL[c.status]}</span>}
            </span>
            {!busy && (
              <button type="button" onClick={() => removeUpload(c.cid)} aria-label={`Remove ${c.name}`} className="flex-none mt-0.5 opacity-70 hover:opacity-100">
                <X className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
