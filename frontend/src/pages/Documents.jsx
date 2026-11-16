import React, { useEffect, useRef, useState } from 'react';
import { FileText, Loader2, Upload, X } from 'lucide-react';
import { apiService } from '../services/api';
import { formatCount, formatSize } from '../lib/format';
import DocumentRow from '../components/DocumentRow';
import QueuePanel from '../components/QueuePanel';
import { openPreview } from '../state/previewStore';
import { onDocumentsChanged } from '../state/documentEvents';

const ACCEPTED = ['.pdf', '.docx', '.txt', '.md'];
const MAX_MB = 20;
const POLL_MS = 2000;
function Notice({ notice, onClose }) {
  const tone = notice.type === 'error'
    ? 'bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]'
    : 'bg-raised text-[color:var(--text-2)]';
  return (
    <div className={`flex items-start gap-3 rounded-control px-3 py-2.5 text-body ${tone}`} role={notice.type === 'error' ? 'alert' : 'status'}>
      <ul className="flex-1 flex flex-col gap-1">
        {notice.lines.map((l, i) => <li key={i}>{l}</li>)}
      </ul>
      <button onClick={onClose} aria-label="Dismiss" className="opacity-70 hover:opacity-100">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

export default function Documents({ onChanged }) {
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [uploading, setUploading] = useState([]); // files waiting or being indexed, in order
  const [notice, setNotice] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef(null);
  const dragDepth = useRef(0);

  const load = async () => {
    try {
      setDocuments(await apiService.getDocuments());
      setLoadError(null);
    } catch (err) {
      setLoadError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);
  // an edit saved in the preview: which edit Undo and Redo apply to has changed
  useEffect(() => onDocumentsChanged(() => load()), []);

  // Indexing runs on the server after the upload returns: check back while any file is still being indexed.
  const indexing = documents.filter((d) => d.status === 'processing').map((d) => d.id).join(',');
  const indexingBefore = useRef('');
  useEffect(() => {
    if (indexingBefore.current && indexing !== indexingBefore.current) onChanged?.(); // one finished
    // "… is being indexed": once nothing is, the rows say it themselves
    if (!indexing) setNotice((n) => (n?.untilIndexed ? null : n));
    indexingBefore.current = indexing;
    if (!indexing) return undefined;
    const timer = setTimeout(load, POLL_MS);
    return () => clearTimeout(timer);
  }, [indexing, documents]);

  const uploadOne = async (file) => {
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!ACCEPTED.includes(ext)) return { ok: false, text: `${file.name}: only PDF, DOCX, TXT and MD files can be added.` };
    if (file.size > MAX_MB * 1024 * 1024) return { ok: false, text: `${file.name}: larger than ${MAX_MB} MB.` };
    try {
      const data = await apiService.uploadDocument(file);
      return {
        ok: true,
        indexing: data.status !== 'duplicate',
        text: data.status === 'duplicate'
          ? `${data.filename} was already added (${formatCount(data.chunks_indexed)} passages).`
          : `${data.filename} is being indexed. It's listed below and can be asked about once it shows Ready.`,
      };
    } catch (err) {
      return { ok: false, text: `${file.name}: ${err.message}` };
    }
  };

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length || uploading.length) return;
    setNotice(null);
    setUploading(files.map((f) => ({ name: f.name, size: f.size })));
    const results = [];
    for (const file of files) {
      results.push(await uploadOne(file));
      setUploading((q) => q.slice(1));
    }
    if (fileInput.current) fileInput.current.value = '';
    const failedSome = results.some((r) => !r.ok);
    setNotice({ type: failedSome ? 'error' : 'success', lines: results.map((r) => r.text), untilIndexed: !failedSome && results.some((r) => r.indexing) });
    await load();
    onChanged?.();
  };

  const remove = async (doc) => {
    try {
      await apiService.deleteDocument(doc.id);
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
      setNotice({ type: 'success', lines: [`Removed “${doc.title}”.`] });   // replaces an older note, which may be about it
      onChanged?.();
    } catch (err) {
      setNotice({ type: 'error', lines: [`${doc.title}: ${err.message}`] });
    }
  };

  // Rename, Undo and Redo: the list is read again afterwards (the name, and which edit Undo/Redo now apply to).
  const changed = async (line) => {
    setNotice({ type: 'success', lines: [line] });
    await load();
    onChanged?.();
  };
  const failed = (line) => setNotice({ type: 'error', lines: [line] });
  const undo = async (doc) => {
    try { await apiService.undoEdit(doc.id, doc.edit_id); }
    catch (err) { failed(`${doc.title}: ${err.message}`); await load(); throw err; }
    await changed(`Undid the latest edit to “${doc.title}”. Redo brings it back.`);
  };
  const redo = async (doc) => {
    try { await apiService.redoEdit(doc.id, doc.redo_id); }
    catch (err) { failed(`${doc.title}: ${err.message}`); await load(); throw err; }
    await changed(`Brought back the edit to “${doc.title}”.`);
  };

  const ready = documents.filter((d) => d.status === 'indexed');
  const passages = ready.reduce((sum, d) => sum + d.chunks, 0);
  const pages = ready.reduce((sum, d) => sum + d.pages, 0);
  const busy = uploading.length > 0;
  const emptyLibrary = !loadError && !loading && documents.length === 0 && !busy;

  return (
    <div
      className="h-full overflow-y-auto relative"
      onDragEnter={(e) => { e.preventDefault(); dragDepth.current += 1; setDragging(true); }}
      onDragOver={(e) => e.preventDefault()}
      onDragLeave={() => { dragDepth.current -= 1; if (dragDepth.current <= 0) setDragging(false); }}
      onDrop={(e) => { e.preventDefault(); dragDepth.current = 0; setDragging(false); handleFiles(e.dataTransfer.files); }}
    >
      <div className="max-w-[1400px] px-4 sm:px-6 lg:px-8 py-8">
        {/* Phone: title, then a full-width button. Tablet: the button on its own row, at the left. Wide: side by side. */}
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <h1 className="text-title font-medium tracking-[-0.01em]">Documents</h1>
            <p className="mt-1 text-body text-muted">
              {loading
                ? 'Loading…'
                : ready.length
                  ? `${ready.length} document${ready.length === 1 ? '' : 's'}, ${formatCount(pages)} pages and ${formatCount(passages)} passages are searched before the web.`
                  : 'Nothing to answer from yet.'}
            </p>
          </div>
          <div className="flex flex-col items-stretch sm:items-start md:items-end gap-1.5 w-full md:w-auto flex-none">
            <button className="btn-primary" onClick={() => fileInput.current?.click()} disabled={busy} aria-describedby="upload-rules">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Upload className="w-4 h-4" aria-hidden="true" />}
              {busy ? 'Adding…' : 'Upload documents'}
            </button>
            <p id="upload-rules" className="text-small text-muted">PDF, DOCX, TXT or MD, up to {MAX_MB} MB each</p>
          </div>
        </div>

        <input ref={fileInput} type="file" multiple accept={ACCEPTED.join(',')} className="hidden"
               onChange={(e) => handleFiles(e.target.files)} />

        {notice && <div className="mt-6"><Notice notice={notice} onClose={() => setNotice(null)} /></div>}

        <QueuePanel onTaskFinished={() => { load(); onChanged?.(); }} />

        <div className={`mt-6 ${emptyLibrary ? '' : 'panel'}`}>
          {loadError && (
            <p className="px-4 py-3 text-body text-[var(--pill-negative-ink)]">
              Couldn't load your documents: {loadError}{' '}
              <button className="font-medium underline underline-offset-4" onClick={load}>Retry</button>
            </p>
          )}

          {emptyLibrary && (
            // First run: the drop zone takes the place the list will fill.
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              className="w-full min-h-[min(480px,60vh)] grid place-items-center content-center gap-3 rounded-card border border-dashed border-control bg-surface px-6 py-12 text-center hover:border-ink transition-colors"
            >
              <FileText className="w-7 h-7 text-muted" strokeWidth={1.4} aria-hidden="true" />
              <span className="text-section font-medium">Drop files here, or choose them</span>
              <span className="text-reading text-muted max-w-[460px]">
                Questions about these documents are then answered from them first, with page references.
                PDF, DOCX, TXT or MD, up to {MAX_MB} MB each.
              </span>
            </button>
          )}

          {(loading || documents.length > 0 || busy) && (
            <table className="w-full text-body">
              <thead className="hidden md:table-header-group">
                <tr className="text-small text-muted">
                  <th className="text-left font-normal px-4 h-10">Name</th>
                  <th className="text-right font-normal px-4">Pages</th>
                  <th className="text-right font-normal px-4">Passages</th>
                  <th className="text-right font-normal px-4">Size</th>
                  <th className="text-right font-normal px-4">Added</th>
                  <th className="text-left font-normal px-4">Status</th>
                  <th className="px-4"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {uploading.map((f, i) => (
                  <tr key={`up-${f.name}-${i}`} className="border-t border-rule first:border-t-0 md:first:border-t">
                    <td className="px-4 py-3" colSpan={3}>
                      <span className="flex items-center gap-2.5 min-w-0">
                        <FileText className="w-4 h-4 flex-none text-muted" strokeWidth={1.6} aria-hidden="true" />
                        <span className="truncate font-medium">{f.name}</span>
                      </span>
                    </td>
                    <td className="px-4 text-right num text-muted hidden md:table-cell">{formatSize(f.size)}</td>
                    <td className="px-4 text-right text-muted hidden md:table-cell">—</td>
                    <td className="px-4 text-right md:text-left">
                      <span className="pill-warning">{i === 0 ? 'Indexing' : 'Waiting'}</span>
                    </td>
                    <td className="px-4 hidden md:table-cell" />
                  </tr>
                ))}

                {loading && [0, 1, 2].map((i) => (
                  <tr key={`sk-${i}`} className="border-t border-rule">
                    <td className="px-4 py-3.5"><span className="skeleton block h-4 w-56" /></td>
                    <td className="px-4 hidden md:table-cell"><span className="skeleton block h-4 w-8 ml-auto" /></td>
                    <td className="px-4 hidden md:table-cell"><span className="skeleton block h-4 w-10 ml-auto" /></td>
                    <td className="px-4 hidden md:table-cell"><span className="skeleton block h-4 w-12 ml-auto" /></td>
                    <td className="px-4 hidden md:table-cell"><span className="skeleton block h-4 w-12 ml-auto" /></td>
                    <td className="px-4 hidden md:table-cell"><span className="skeleton block h-4 w-12" /></td>
                    <td className="px-4"><span className="skeleton block h-4 w-14 ml-auto" /></td>
                  </tr>
                ))}

                {documents.map((doc) => (
                  <DocumentRow
                    key={doc.id} doc={doc} onPreview={openPreview} onUndo={undo} onRedo={redo} onRemove={remove}
                    onError={failed}
                    onRenamed={(r) => !r.unchanged && changed(`Renamed “${r.old_title}” to “${r.new_title}”.`)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {dragging && (
        <div className="absolute inset-3 z-10 grid place-items-center rounded-card border border-dashed border-ink bg-ground/95 pointer-events-none">
          <p className="text-section font-medium">{busy ? 'Wait for the current upload to finish' : 'Drop to add these files'}</p>
        </div>
      )}
    </div>
  );
}
