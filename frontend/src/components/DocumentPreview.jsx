/** The one document preview: a PDF in the pdf.js viewer (lib/pdfViewer.js), any other document as the PDF it is
    downloaded as, so its pages are the real ones. Mounted once in App; opened with openPreview(doc). */
import React, { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { API_BASE_URL } from '../services/api';
import { documentFiles } from '../lib/documentFiles';
import { closePreview, usePreview } from '../state/previewStore';
import DocumentLinks from './DocumentLinks';

/** documents: the library as last listed, so a preview opened from an old chat shows the current title and type. */
export default function DocumentPreview({ documents = [] }) {
  const asked = usePreview();
  const known = asked && documents.find((d) => d.id === asked.id);
  const doc = asked && { ...asked, ...(known ? { title: known.title, ext: known.ext } : {}) };
  const host = useRef(null);

  useEffect(() => {
    if (!doc) return undefined;
    let viewer = null;
    let cancelled = false;
    const files = documentFiles(API_BASE_URL, doc);
    import('../lib/pdfViewer').then(({ createPdfViewer }) => {   // its code and pdf.js load on the first preview
      if (cancelled || !host.current) return;
      viewer = createPdfViewer({ url: files.previewUrl, downloadUrl: files.pdfUrl, fileName: `${doc.title}.pdf` });
      host.current.replaceChildren(viewer.element);
    });
    const onKey = (e) => { if (e.key === 'Escape') closePreview(); };
    window.addEventListener('keydown', onKey);
    return () => { cancelled = true; viewer?.destroy(); window.removeEventListener('keydown', onKey); };
  }, [asked?.id, doc?.ext]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!doc) return null;
  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-stretch justify-center p-0 sm:p-6" onMouseDown={(e) => {
      if (e.target === e.currentTarget) closePreview();
    }}>
      <div role="dialog" aria-modal="true" aria-label={`Preview of ${doc.title}`}
           className="bg-surface w-full max-w-[1000px] sm:rounded-card border border-line flex flex-col min-h-0 overflow-hidden">
        <div className="flex-none flex items-center gap-3 h-12 px-4 border-b border-line">
          <h2 className="flex-1 truncate text-section font-medium" title={doc.title}>{doc.title}</h2>
          <DocumentLinks doc={doc} />
          <button type="button" className="btn-ghost h-8 w-8 p-0 -mr-2" onClick={closePreview} aria-label="Close preview">
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
        {doc.ext && doc.ext !== '.pdf' && (
          // the original's fonts, images and page breaks aren't recreated, so its page count can differ
          <p className="flex-none px-4 py-1.5 border-b border-line text-small text-muted">
            The text in a simple layout. Download the file to see it as it was made.
          </p>
        )}
        <div ref={host} className="flex-1 min-h-0"><p className="p-6 text-body text-muted">Loading…</p></div>
      </div>
    </div>
  );
}
