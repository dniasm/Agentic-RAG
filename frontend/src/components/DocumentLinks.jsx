/** Download links for a library document: its own format, and (for a Word, text or Markdown file) a PDF copy. */
import React from 'react';
import { API_BASE_URL } from '../services/api';
import { documentFiles } from '../lib/documentFiles';

export default function DocumentLinks({ doc, className = '' }) {
  const files = documentFiles(API_BASE_URL, doc);
  const type = (doc.ext || '').replace('.', '').toUpperCase();
  return (
    <span className={`inline-flex items-center gap-3 ${className}`}>
      <a className="link-action" href={files.downloadUrl} download title={`Download the ${type || 'file'}`}>Download</a>
      {doc.ext && doc.ext !== '.pdf' && (
        <a className="link-action" href={files.pdfUrl} download title="Download as a PDF (simple layout)">PDF</a>
      )}
    </span>
  );
}
