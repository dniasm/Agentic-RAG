// src/pages/Documents.jsx
import React, { useState, useRef, useEffect } from 'react';
import { UploadCloud, FileText, CheckCircle2, AlertCircle, Loader2, Trash2 } from 'lucide-react';
import { apiService } from '../services/api';

const ACCEPTED = ['.pdf', '.docx', '.txt', '.md'];
const MAX_MB = 20;

const formatSize = (bytes) =>
  bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;

const STATUS_STYLES = {
  indexed: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  processing: 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  failed: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
};

export default function Documents() {
  const [documents, setDocuments] = useState([]);
  const [loadingList, setLoadingList] = useState(true);
  const [uploading, setUploading] = useState(null); // filename currently being ingested
  const [uploadMessage, setUploadMessage] = useState(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef(null);

  const loadDocuments = async () => {
    try {
      setDocuments(await apiService.getDocuments());
    } catch (err) {
      setUploadMessage({ type: 'error', text: err.message });
    } finally {
      setLoadingList(false);
    }
  };

  useEffect(() => { loadDocuments(); }, []);

  const uploadOne = async (file) => {
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    if (!ACCEPTED.includes(ext)) {
      return { type: 'error', text: `${file.name}: unsupported type (allowed: ${ACCEPTED.join(', ')}).` };
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      return { type: 'error', text: `${file.name}: larger than ${MAX_MB} MB.` };
    }
    setUploading(file.name);
    try {
      const data = await apiService.uploadDocument(file);
      return data.status === 'duplicate'
        ? { type: 'success', text: `"${data.filename}" is already indexed (${data.chunks_indexed} chunks).` }
        : { type: 'success', text: `Ingested "${data.filename}" — ${data.chunks_indexed} chunks embedded into Qdrant.` };
    } catch (err) {
      return { type: 'error', text: `${file.name}: ${err.message}` };
    }
  };

  const handleFiles = async (fileList) => {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    setUploadMessage(null);
    const results = [];
    for (const file of files) {
      results.push(await uploadOne(file));
    }
    setUploading(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
    const failed = results.filter((r) => r.type === 'error');
    setUploadMessage({
      type: failed.length ? 'error' : 'success',
      text: results.map((r) => r.text).join('\n'),
    });
    loadDocuments();
  };

  const handleDelete = async (doc) => {
    if (!window.confirm(`Remove "${doc.filename}" and its vectors from the knowledge base?`)) return;
    try {
      await apiService.deleteDocument(doc.id);
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (err) {
      setUploadMessage({ type: 'error', text: err.message });
    }
  };

  return (
    <div className="flex-1 p-8 text-slate-200 overflow-y-auto">
      <h2 className="text-2xl font-bold mb-6">Document Manager</h2>

      <input
        type="file"
        ref={fileInputRef}
        multiple
        onChange={(e) => handleFiles(e.target.files)}
        accept={ACCEPTED.join(',')}
        className="hidden"
      />

      {/* Drag & Drop Upload Zone */}
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); handleFiles(e.dataTransfer.files); }}
        onClick={() => !uploading && fileInputRef.current?.click()}
        className={`border-2 border-dashed transition rounded-xl p-8 flex flex-col items-center justify-center bg-slate-900/40 mb-6 ${
          uploading ? 'cursor-wait border-indigo-500/50' : 'cursor-pointer'
        } ${dragging ? 'border-indigo-500 bg-indigo-500/5' : 'border-slate-800 hover:border-indigo-500/50'}`}
      >
        {uploading ? (
          <Loader2 className="w-10 h-10 text-indigo-400 mb-2 animate-spin" />
        ) : (
          <UploadCloud className="w-10 h-10 text-indigo-400 mb-2" />
        )}
        <p className="text-sm font-medium">
          {uploading ? `Chunking, embedding and indexing "${uploading}"…` : 'Click to upload or drag & drop files'}
        </p>
        <p className="text-xs text-slate-500 mt-1">PDF, DOCX, TXT or MD — up to {MAX_MB} MB each</p>
      </div>

      {uploadMessage && (
        <div
          className={`mb-6 p-4 rounded-xl flex items-start space-x-3 text-sm ${
            uploadMessage.type === 'error'
              ? 'bg-red-950/40 text-red-400 border border-red-900/50'
              : 'bg-emerald-950/40 text-emerald-400 border border-emerald-900/50'
          }`}
        >
          {uploadMessage.type === 'error' ? (
            <AlertCircle className="w-5 h-5 flex-shrink-0 text-red-400" />
          ) : (
            <CheckCircle2 className="w-5 h-5 flex-shrink-0 text-emerald-400" />
          )}
          <span className="whitespace-pre-line">{uploadMessage.text}</span>
        </div>
      )}

      {/* Indexed Documents */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden">
        <div className="p-4 border-b border-slate-800 font-semibold text-white flex items-center justify-between">
          <span>Active Knowledge Base</span>
          <span className="text-xs font-normal text-slate-500">
            {documents.filter((d) => d.status === 'indexed').length} indexed
          </span>
        </div>
        <div className="divide-y divide-slate-800/60">
          {loadingList && (
            <div className="p-6 flex justify-center"><Loader2 className="w-5 h-5 animate-spin text-slate-500" /></div>
          )}
          {!loadingList && documents.length === 0 && (
            <p className="p-6 text-sm text-slate-500 text-center">No documents yet. Upload one to enable RAG answers.</p>
          )}
          {documents.map((doc) => (
            <div key={doc.id} className="p-4 flex items-center justify-between hover:bg-slate-800/30 transition group">
              <div className="flex items-center space-x-3 min-w-0">
                <FileText className="w-5 h-5 text-indigo-400 flex-shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-white truncate">{doc.filename}</p>
                  <p className="text-xs text-slate-500">
                    {doc.chunks} vector chunks · {doc.pages} page{doc.pages === 1 ? '' : 's'} · {formatSize(doc.size_bytes)}
                    {doc.error && <span className="text-rose-400"> · {doc.error}</span>}
                  </p>
                </div>
              </div>
              <div className="flex items-center space-x-3 flex-shrink-0">
                <span className={`px-2.5 py-1 rounded-md text-xs border font-medium capitalize ${STATUS_STYLES[doc.status] || STATUS_STYLES.processing}`}>
                  {doc.status}
                </span>
                <button
                  onClick={() => handleDelete(doc)}
                  className="p-1.5 rounded text-slate-500 hover:text-rose-400 hover:bg-rose-500/10 transition"
                  title="Delete document"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
