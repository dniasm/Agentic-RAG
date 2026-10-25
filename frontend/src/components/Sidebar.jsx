import React from 'react';
import { MessageSquare, FileText, LayoutDashboard, Plus, LogOut, Cpu, Trash2, User } from 'lucide-react';

export default function Sidebar({
  user,
  activePage,
  setActivePage,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewChat,
  onDeleteConversation,
  onLogout,
}) {
  const navClass = (active) =>
    `w-full text-left px-3 py-2 rounded-lg text-sm transition flex items-center space-x-2 ${
      active ? 'bg-slate-800 text-indigo-400 font-medium' : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-200'
    }`;

  return (
    <aside className="w-64 bg-slate-900 text-slate-200 flex flex-col border-r border-slate-800 p-4 select-none">
      {/* Brand Header */}
      <div className="flex items-center space-x-3 px-2 mb-6">
        <Cpu className="w-7 h-7 text-indigo-400" />
        <span className="font-bold text-lg text-white tracking-wide">Agentic RAG</span>
      </div>

      {/* New Chat Button */}
      <button
        onClick={onNewChat}
        className="w-full bg-indigo-600 hover:bg-indigo-500 text-white font-medium py-2 px-4 rounded-xl flex items-center justify-center space-x-2 transition shadow-md hover:shadow-indigo-500/20"
      >
        <Plus className="w-4 h-4" />
        <span>New Chat</span>
      </button>

      {/* Conversations */}
      <div className="mt-6 flex-1 min-h-0 flex flex-col">
        <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider px-2 mb-2">Chats</h4>
        <nav className="space-y-1 overflow-y-auto flex-1 pr-1">
          {conversations.length === 0 && (
            <p className="px-3 py-2 text-xs text-slate-500">No conversations yet.</p>
          )}
          {conversations.map((conv) => (
            <div key={conv.id} className="group relative">
              <button
                onClick={() => onSelectConversation(conv.id)}
                className={navClass(activePage === 'chat' && activeConversationId === conv.id)}
                title={conv.title}
              >
                <MessageSquare className="w-4 h-4 flex-shrink-0" />
                <span className="truncate pr-5">{conv.title}</span>
              </button>
              <button
                onClick={() => onDeleteConversation(conv.id)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded text-slate-500 hover:text-rose-400 opacity-0 group-hover:opacity-100 transition"
                title="Delete chat"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </nav>
      </div>

      {/* Secondary Pages Navigation */}
      <div className="pt-4 mt-4 border-t border-slate-800 space-y-1">
        <button onClick={() => setActivePage('documents')} className={navClass(activePage === 'documents')}>
          <FileText className="w-4 h-4" />
          <span>Documents</span>
        </button>
        <button onClick={() => setActivePage('dashboard')} className={navClass(activePage === 'dashboard')}>
          <LayoutDashboard className="w-4 h-4" />
          <span>Dashboard</span>
        </button>
      </div>

      {/* User + Logout */}
      <div className="pt-4 mt-4 border-t border-slate-800 space-y-1">
        <div className="px-3 py-1 flex items-center space-x-2 text-sm text-slate-300">
          <User className="w-4 h-4 text-slate-500" />
          <span className="truncate">{user?.username}</span>
        </div>
        <button
          onClick={onLogout}
          className="w-full text-left px-3 py-2 rounded-lg text-sm text-rose-400 hover:bg-rose-500/10 transition flex items-center space-x-2"
        >
          <LogOut className="w-4 h-4" />
          <span>Log Out</span>
        </button>
      </div>
    </aside>
  );
}
