/** The one document preview: a PDF in the pdf.js viewer (lib/pdfViewer.js), any other document as the PDF it is
    downloaded as, so its pages are the real ones. Mounted once in App; opened with openPreview(doc).

    Editing here: "Edit a passage" replaces a passage (SelectionEditor, the same edit as the chat's); a passage
    selected on the page fills it in ("Edit selection"), or it can be typed, so the keyboard reaches it too. A TXT or
    Markdown file also opens whole in "Edit text" (TextEditor). After a save the page is drawn again where the reader
    was, a note says what changed (with Undo), and the document lists are told (state/documentEvents). Closing with
    unsaved changes asks first. A phone's Back closes an open editor first, then the preview (state/previewStore). */
import React, { useEffect, useRef, useState } from 'react';
import { Pencil, X } from 'lucide-react';
import { API_BASE_URL, apiService } from '../services/api';
import { documentFiles } from '../lib/documentFiles';
import { LAYOUT_NOTE } from '../lib/chatActions';
import { closePreview, onPreviewBack, usePreview } from '../state/previewStore';
import { documentsChanged } from '../state/documentEvents';
import ConfirmAction from './ConfirmAction';
import DocumentLinks from './DocumentLinks';
import SelectionEditor from './SelectionEditor';
import TextEditor from './TextEditor';

const WHOLE_TEXT = ['.txt', '.md'];
const DISCARD = 'Discard your changes?';

/** The text selected inside `root`, or '' (a selection elsewhere on the page doesn't count). */
function selectionIn(root) {
  const sel = window.getSelection();
  if (!root || !sel || sel.isCollapsed || !root.contains(sel.anchorNode) || !root.contains(sel.focusNode)) return '';
  return sel.toString().trim();
}

/** What a save changed, as the note's lines. */
function savedLines(result, what) {
  const lines = [what];
  if (result.converted) lines.push('The PDF was converted to a Markdown copy to make this change.');
  if (LAYOUT_NOTE[result.layout]) lines.push(LAYOUT_NOTE[result.layout]);
  for (const [font, drawn] of Object.entries(result.stand_in || {})) {   // { PDF's font: look-alike drawn instead }
    lines.push(`${font} isn't available here, so the new text uses ${drawn}, which has the same letter widths.`);
  }
  if (result.added_lines) lines.push(`The text grew by ${result.added_lines} line${result.added_lines === 1 ? '' : 's'}: check the page.`);
  return lines;
}

/** documents: the library as last listed, so a preview opened from an old chat shows the current title and type. */
export default function DocumentPreview({ documents = [] }) {
  const asked = usePreview();
  const known = asked && documents.find((d) => d.id === asked.id);
  const [extNow, setExtNow] = useState(null);     // the type after a save (a converted PDF is Markdown from then on)
  const doc = asked && { ...asked, ...(known ? { title: known.title, ext: known.ext } : {}), ...(extNow ? { ext: extNow } : {}) };
  const host = useRef(null);
  const viewer = useRef(null);
  const scrollBack = useRef(0);                   // where the reader was when the page is drawn again
  const [version, setVersion] = useState(0);      // drawn again after each save
  const [mode, setMode] = useState('view');       // view | selection | text
  const [selected, setSelected] = useState('');   // the passage selected on the page now
  const [editing, setEditing] = useState('');     // the passage being replaced ('' : typed in the editor)
  const [dirty, setDirty] = useState(false);      // an editor holds unsaved text
  const [note, setNote] = useState(null);         // after a save: { lines, editId }
  // the latest of these for the Back handler, which runs outside React's render
  const modeNow = useRef('view');
  const dirtyNow = useRef(false);
  const closing = useRef(false);                  // X or Escape asked to close: Back closes, no second question
  modeNow.current = mode;
  dirtyNow.current = dirty;

  // a new document: start over
  useEffect(() => {
    setMode('view'); setSelected(''); setNote(null); setExtNow(null); setDirty(false); scrollBack.current = 0;
    closing.current = false;
  }, [asked?.id]);

  // Back (a phone's gesture): an open editor closes first (asking about unsaved text), then the preview
  useEffect(() => onPreviewBack(() => {
    if (closing.current) { closing.current = false; return true; }
    if (modeNow.current === 'view') return true;
    if (!dirtyNow.current || window.confirm(DISCARD)) { setDirty(false); setMode('view'); }
    return false;   // the preview stays
  }), []);

  useEffect(() => {
    if (!doc || mode === 'text') return undefined;
    let cancelled = false;
    const files = documentFiles(API_BASE_URL, doc);
    import('../lib/pdfViewer').then(({ createPdfViewer }) => {   // its code and pdf.js load on the first preview
      if (cancelled || !host.current) return;
      viewer.current = createPdfViewer({ url: files.previewUrl, downloadUrl: files.pdfUrl, fileName: `${doc.title}.pdf`,
                                         initialScroll: scrollBack.current });
      host.current.replaceChildren(viewer.current.element);
    });
    return () => { cancelled = true; viewer.current?.destroy(); viewer.current = null; };
  }, [asked?.id, doc?.ext, version, mode === 'text']); // eslint-disable-line react-hooks/exhaustive-deps

  // unsaved text: the browser asks before the tab is closed or reloaded
  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (e) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const leave = (then) => {   // out of an editor, or the preview: asks first when there is unsaved text
    if (dirtyNow.current && !window.confirm(DISCARD)) return;
    dirtyNow.current = false;
    setDirty(false);
    then();
  };
  const close = () => leave(() => { closing.current = true; closePreview(); });
  const toView = () => leave(() => setMode('view'));

  useEffect(() => {
    if (!doc) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && mode === 'view') close(); };
    const onSelect = () => setSelected(selectionIn(host.current));
    window.addEventListener('keydown', onKey);
    document.addEventListener('selectionchange', onSelect);
    return () => { window.removeEventListener('keydown', onKey); document.removeEventListener('selectionchange', onSelect); };
  }, [asked?.id, mode, dirty]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!doc) return null;

  const redraw = () => {
    scrollBack.current = viewer.current?.getScroll() ?? scrollBack.current;
    setVersion((v) => v + 1);
  };

  const saved = (result, what) => {
    const now = (result.documents || []).find((d) => d.id === doc.id);
    if (now?.ext && now.ext !== doc.ext) setExtNow(now.ext);   // drawn as what it is now, before the lists catch up
    setNote({ lines: savedLines(result, what), editId: result.edit_id });
    setDirty(false);
    setMode('view');
    setEditing('');
    setSelected('');
    window.getSelection()?.removeAllRanges();
    redraw();
    documentsChanged(doc.id);
  };

  const undo = async () => {
    try {
      const result = await apiService.undoEdit(doc.id, note.editId);
      const now = (result.documents || []).find((d) => d.id === doc.id);
      if (now?.ext) setExtNow(now.ext);   // undoing a conversion brings the PDF back
      setNote({ lines: ['The edit was undone. Redo is on the Documents page and in the chat.'], editId: null });
      redraw();
      documentsChanged(doc.id);
    } catch (err) {
      setNote({ lines: [`Couldn't undo: ${err.message}`], editId: null });
      throw err;
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-stretch justify-center p-0 sm:p-6" onMouseDown={(e) => {
      if (e.target === e.currentTarget && mode === 'view') close();
    }}>
      <div role="dialog" aria-modal="true" aria-label={`Preview of ${doc.title}`}
           className="bg-surface w-full min-w-0 max-w-[1000px] sm:rounded-card border border-line flex flex-col min-h-0 overflow-hidden">
        <div className="flex-none flex items-center gap-3 min-h-12 px-4 py-2 border-b border-line flex-wrap">
          <h2 className="flex-1 min-w-0 truncate text-section font-medium" title={doc.title}>{doc.title}</h2>
          {mode === 'view' && (
            // mousedown would clear a selection before the click: keep it
            <button type="button" className={selected ? 'btn-secondary h-8' : 'link-action'}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => { setEditing(selected); setMode('selection'); setNote(null); }}>
              <Pencil className="w-4 h-4" aria-hidden="true" />{selected ? 'Edit selection' : 'Edit a passage'}
            </button>
          )}
          {mode === 'view' && WHOLE_TEXT.includes(doc.ext) && (
            <button type="button" className="link-action" onClick={() => { setMode('text'); setNote(null); }}>Edit text</button>
          )}
          {mode === 'view' && <DocumentLinks doc={doc} />}
          <button type="button" className="btn-ghost h-8 w-8 p-0 -mr-2" onClick={close} aria-label="Close preview">
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>
        {mode === 'view' && !note && (
          <p className="flex-none px-4 py-1.5 border-b border-line text-small text-muted">
            {doc.ext && doc.ext !== '.pdf'
              ? 'The text in a simple layout. Select a passage to change it, or download the file to see it as it was made.'
              : 'Select a passage to change it.'}
          </p>
        )}
        {note && (
          <div className="flex-none flex flex-wrap items-baseline gap-x-3 gap-y-1 px-4 py-2 border-b border-line bg-raised text-body" role="status">
            <span className="flex-1 min-w-0">{note.lines.map((l, i) => <span key={i} className="block">{l}</span>)}</span>
            {note.editId != null && (
              <ConfirmAction label="Undo" question="Undo this edit?" busyLabel="Undoing…" onConfirm={undo} />
            )}
            <button type="button" className="text-muted hover:text-ink" onClick={() => setNote(null)} aria-label="Dismiss">
              <X className="w-4 h-4" aria-hidden="true" />
            </button>
          </div>
        )}
        {mode === 'text' ? (
          <TextEditor doc={doc} onCancel={toView} onDirtyChange={setDirty}
                      onSaved={(r) => saved(r, 'Saved. The document was indexed again.')} />
        ) : (
          <div ref={host} className="flex-1 min-h-0 min-w-0"><p className="p-6 text-body text-muted">Loading…</p></div>
        )}
        {mode === 'selection' && (
          <SelectionEditor doc={doc} selected={editing} onCancel={toView} onDirtyChange={setDirty}
                           onSaved={(r) => saved(r, r.changes > 1 ? `Changed ${r.changes} places.` : 'Changed.')} />
        )}
      </div>
    </div>
  );
}
