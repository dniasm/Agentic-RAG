import React, { useState } from 'react';
import { FileText, Globe, ChevronDown, ChevronRight } from 'lucide-react';

export default function SourceCard({ sources }) {
  const [open, setOpen] = useState(false);
  if (!sources || sources.length === 0) return null;

  const isWeb = sources[0].type === 'web';

  return (
    <div className="mt-3 bg-slate-950/40 border border-slate-800 rounded-lg p-3 text-xs space-y-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="text-slate-400 font-medium flex items-center space-x-1 hover:text-slate-200"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        {isWeb ? <Globe className="w-3.5 h-3.5 text-amber-400" /> : <FileText className="w-3.5 h-3.5 text-indigo-400" />}
        <span>{sources.length} {isWeb ? 'web sources' : 'sources retrieved'}</span>
      </button>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {sources.map((src, i) => (
          <div key={i} className="bg-slate-800/60 p-2 rounded border border-slate-700/50 min-w-0">
            <div className="flex items-center justify-between space-x-2">
              <span className="text-slate-500 font-mono">[{i + 1}]</span>
              {src.type === 'web' ? (
                <a
                  href={src.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-amber-300 hover:underline truncate flex-1"
                  title={src.url}
                >
                  {src.title}
                </a>
              ) : (
                <span className="text-slate-300 truncate font-mono flex-1" title={src.document}>{src.document}</span>
              )}
              {src.page != null && <span className="text-slate-500 text-[10px] flex-shrink-0">p. {src.page}</span>}
              {src.score != null && (
                <span className="text-slate-500 text-[10px] flex-shrink-0">{src.score.toFixed(2)}</span>
              )}
            </div>
            {open && src.snippet && <p className="mt-1.5 text-slate-400 leading-snug line-clamp-4">{src.snippet}</p>}
          </div>
        ))}
      </div>
    </div>
  );
}
