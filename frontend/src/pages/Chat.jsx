import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { ArrowRight, Loader2, PanelRight, Upload } from 'lucide-react';
import ChatDocuments from '../components/ChatDocuments';
import InputBox from '../components/InputBox';
import UploadChips from '../components/UploadChips';
import Message from '../components/Message';
import { hrefFor } from '../lib/route';
import { redoableEdits, undoableEdits } from '../lib/chatActions';
import { readStored, writeStored } from '../lib/storage';
import { apiService } from '../services/api';
import { useChatDocuments } from '../state/chatDocuments';
import { addFiles, moveUploads, useUploads } from '../state/chatUploads';
import { chipsFor, shouldJumpToBottom } from '../lib/uploadChips';
import { NEW_KEY, clearNewChat, loadConversation, sendMessage, useConversation } from '../state/chatStore';

function suggestionsFor(documents) {
  const first = documents?.[0]?.title;
  return [
    first ? `Summarise ${first}` : 'Explain how retrieval-augmented generation works',
    first && documents.length > 1
      ? `Compare what ${first} and ${documents[1].title} say about their main topic`
      : 'What is the difference between BM25 and dense retrieval?',
    'What are the latest developments in open-source language models?',
  ];
}

function NewChat({ documents, onSuggestion }) {
  const count = documents?.length ?? 0;
  return (
    <div className="max-w-[760px] mx-auto w-full min-h-full flex flex-col justify-center py-10">
      <h2 className="text-title font-medium tracking-[-0.01em]">What would you like to know?</h2>
      <p className="mt-2 text-reading text-muted max-w-[620px]">
        {count > 0
          ? `Questions about your ${count} document${count === 1 ? '' : 's'} are answered from them, with page references. `
          : 'Upload documents and questions about them are answered from them, with page references. '}
        Anything recent goes to a web search, and the rest comes from the model's own knowledge. Each answer
        says which one it used.
      </p>

      <ul className="mt-8 panel" aria-label="Suggested questions">
        {suggestionsFor(documents).map((s) => (
          <li key={s} className="border-t border-rule first:border-t-0">
            <button
              type="button"
              onClick={() => onSuggestion(s)}
              className="w-full flex items-center gap-3 px-4 h-12 text-left text-reading hover:bg-ground transition-colors first:rounded-t-card last:rounded-b-card"
            >
              <span className="flex-1 truncate">{s}</span>
              <ArrowRight className="w-4 h-4 text-muted flex-none" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>

      {count === 0 && (
        <p className="mt-4 text-body text-muted">
          No documents yet.{' '}
          <a href={hrefFor('documents')} className="font-medium text-ink underline-offset-4 hover:underline">Upload one</a>
          {' '}to ask questions about it.
        </p>
      )}
    </div>
  );
}

const PANEL_KEY = 'chat-documents-panel-open';

export default function Chat({ conversationId, title, documents, onConversationCreated, onAnswered, onDelete }) {
  const key = conversationId || NEW_KEY;
  const conv = useConversation(key);
  const scrollRef = useRef(null);
  const positionedFor = useRef(null);   // the chat the view has been put at the latest message for
  const dragDepth = useRef(0);
  const [dragging, setDragging] = useState(false);
  const uploads = useUploads();
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => readStored(PANEL_KEY, false));
  const [undone, setUndone] = useState(() => new Set());   // edit ids taken back with an Undo button here
  const [undoError, setUndoError] = useState(null);
  const [buttonChanges, setButtonChanges] = useState(0);   // Undo and Redo clicks: the panel and buttons look again
  const togglePanel = (open) => { setPanelOpen(open); writeStored(PANEL_KEY, open); };
  // fetched again after each answer and each Undo; the Undo buttons go by the server's latest edit of each document
  const chatDocs = useChatDocuments(conversationId, `${conv.messages.length}:${conv.streaming}:${buttonChanges}`);
  const undoable = useMemo(() => undoableEdits(chatDocs.docs, undone), [chatDocs.docs, undone]);
  const redoable = useMemo(() => redoableEdits(chatDocs.docs), [chatDocs.docs]);

  const undo = async (action) => {
    setUndoError(null);
    try {
      await apiService.undoEdit(action.doc_id, action.edit_id);
      setUndone((s) => new Set(s).add(action.edit_id));
      onAnswered?.();   // the document's text, size and pages changed
    } catch (err) {
      if (err.status === 409) setUndone((s) => new Set(s).add(action.edit_id));   // already undone, or superseded
      setUndoError(`Couldn't undo the edit to “${action.title}”: ${err.message}`);
      throw err;
    } finally {
      setButtonChanges((n) => n + 1);
    }
  };

  const redo = async (action) => {
    setUndoError(null);
    try {
      await apiService.redoEdit(action.doc_id, action.edit_id);
      setUndone((s) => { const next = new Set(s); next.delete(action.edit_id); return next; });   // undoable again
      onAnswered?.();
    } catch (err) {
      setUndoError(`Couldn't bring back the edit to “${action.title}”: ${err.message}`);
      throw err;
    } finally {
      setButtonChanges((n) => n + 1);
    }
  };

  useEffect(() => {
    if (conversationId) loadConversation(conversationId);
    else clearNewChat();
  }, [conversationId]);

  // Opening a chat puts the view at its latest message, at once and also when the messages arrive later.
  // (Before paint, so the top is never shown.)
  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el || !shouldJumpToBottom({ key, positionedFor: positionedFor.current, messageCount: conv.messages.length })) return;
    positionedFor.current = key;
    el.scrollTop = el.scrollHeight;
  }, [key, conv.messages.length]);

  // Follow the answer as it is written, unless the reader has scrolled up to read something earlier.
  const lastText = conv.messages.at(-1);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 160;
    if (nearBottom || lastText?.sender === 'user') el.scrollTop = el.scrollHeight;
  }, [conv.messages.length, lastText?.draft, lastText?.text]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = (text) => sendMessage(text, conversationId, {
    onCreated: (id) => { moveUploads(key, id); onConversationCreated?.(id); },
    onDone: onAnswered,
  });
  const addToLibrary = (files) => addFiles(files, key, conversationId);

  const hasFiles = (e) => Array.from(e.dataTransfer?.types || []).includes('Files');
  const drag = {
    onDragEnter: (e) => { if (!hasFiles(e)) return; e.preventDefault(); dragDepth.current += 1; setDragging(true); },
    onDragOver: (e) => { if (hasFiles(e)) e.preventDefault(); },
    onDragLeave: (e) => { if (!hasFiles(e)) return; dragDepth.current -= 1; if (dragDepth.current <= 0) { dragDepth.current = 0; setDragging(false); } },
    onDrop: (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      addToLibrary(e.dataTransfer.files);
    },
  };

  const empty = conv.messages.length === 0;

  const panelButton = (
    <button type="button" className="btn-ghost h-8 px-2 gap-1.5 text-muted hover:text-ink" aria-pressed={panelOpen}
            onClick={() => togglePanel(!panelOpen)} title="Documents changed in this chat">
      <PanelRight className="w-4 h-4" aria-hidden="true" /> <span>Documents</span>
    </button>
  );

  return (
    <div className="h-full flex">
    <div className="flex-1 min-w-0 h-full flex flex-col relative" {...drag}>
      {dragging && (
        <div className="absolute inset-2 z-20 flex items-center justify-center gap-2 rounded-card border-2 border-dashed border-control bg-ground/90 text-reading pointer-events-none">
          <Upload className="w-5 h-5" aria-hidden="true" /> Drop files to add them to your library
        </div>
      )}
      {/* On a phone the top bar already names the chat, so this row is only read by screen readers there. */}
      <div className="sr-only lg:not-sr-only flex-none border-b border-line lg:px-8">
       <div className="max-w-[760px] mx-auto flex items-center gap-4 h-14">
        <h1 className="flex-1 truncate text-section font-medium">{title}</h1>
        {!confirmingDelete && <span className="hidden lg:inline-flex">{panelButton}</span>}
        {conversationId && !confirmingDelete && (
          // hidden on a phone: there a chat is deleted from the menu's chat list
          <button className="hidden lg:inline-flex btn-ghost h-8 -mr-3 text-muted hover:text-ink" onClick={() => setConfirmingDelete(true)}>
            Delete chat
          </button>
        )}
        {conversationId && confirmingDelete && (
          <div className="flex items-center gap-2 text-body">
            <span className="text-muted hidden sm:inline">Delete this chat and its messages?</span>
            <button className="btn-danger h-8" onClick={() => onDelete(conversationId)}>Delete</button>
            <button className="btn-ghost h-8" onClick={() => setConfirmingDelete(false)}>Cancel</button>
          </div>
        )}
       </div>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto px-4 sm:px-6 lg:px-8">
        {conv.loading && empty && (
          <p className="max-w-[760px] mx-auto pt-10 flex items-center gap-2 text-body text-muted">
            <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> Loading the conversation…
          </p>
        )}
        {conv.error && (
          <p className="max-w-[760px] mx-auto mt-10 rounded-control px-3 py-2.5 text-body bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]">
            Couldn't load this conversation: {conv.error}{' '}
            <button className="font-medium underline underline-offset-4" onClick={() => loadConversation(conversationId)}>Retry</button>
          </p>
        )}
        {empty && !conv.loading && !conv.error && <NewChat documents={documents} onSuggestion={send} />}
        {!empty && (
          <div className="max-w-[760px] mx-auto flex flex-col gap-8 py-8">
            {conv.messages.map((m) => <Message key={m.id} msg={m} undoable={undoable} redoable={redoable} onUndo={undo} onRedo={redo} />)}
          </div>
        )}
      </div>

      <div className="flex-none px-4 sm:px-6 lg:px-8 pb-4 pt-2">
        <div className="max-w-[760px] mx-auto">
          {undoError && (
            <p role="alert" className="mb-2 rounded-control px-3 py-2 text-body bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]">
              {undoError}
            </p>
          )}
          <div className="lg:hidden flex justify-end mb-1">{panelButton}</div>
          <InputBox onSend={send} busy={conv.streaming} conversationKey={key} onFiles={addToLibrary}
                    attachments={<UploadChips chips={chipsFor(uploads, key)} />} />
        </div>
      </div>
    </div>
    {panelOpen && (
      <>
        {/* phone and tablet: the panel slides over the chat; a tap beside it closes it */}
        <div className="lg:hidden fixed inset-0 z-30 bg-black/30" onClick={() => togglePanel(false)} aria-hidden="true" />
        <div className="fixed inset-y-0 right-0 z-30 w-[min(320px,88vw)] lg:static lg:z-auto lg:w-[300px] lg:flex-none">
          <ChatDocuments docs={chatDocs.docs} error={chatDocs.error} onClose={() => togglePanel(false)} />
        </div>
      </>
    )}
    </div>
  );
}
