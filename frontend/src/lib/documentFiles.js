/** Where a library document's files are served. Framework-free: { previewUrl, downloadUrl, pdfUrl } for a
    document { id, ext }, under the API's base path. */

/** base: the API's base path, e.g. "/api". */
export function documentFiles(base, doc) {
  const root = `${base}/library/documents/${doc.id}`;
  const isPdf = doc.ext === '.pdf';
  return {
    // what the viewer shows: a PDF as stored, anything else laid out as a PDF (so its pages are the real ones).
    // The time keeps a browser from showing a copy from before an edit.
    previewUrl: `${isPdf ? `${root}/file?inline=true&` : `${root}/pdf?`}v=${Date.now()}`,
    downloadUrl: `${root}/file`,                 // the document in its own format
    pdfUrl: isPdf ? `${root}/file` : `${root}/pdf`,
  };
}
