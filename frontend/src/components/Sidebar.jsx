import React, { useState } from 'react';
import { BarChart3, FileText, LogOut, MessageSquare, Monitor, Moon, Plus, Sun, Trash2 } from 'lucide-react';
import Mark from './Mark';
import { groupByRecency } from '../lib/format';
import { hrefFor } from '../lib/route';
import { useStreamingKeys } from '../state/chatStore';
import { useTheme } from '../lib/theme';

const THEME_OPTIONS = [
  { value: 'system', label: 'System', icon: Monitor },
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
];

function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  return (
    <div role="radiogroup" aria-label="Theme" className="flex gap-0.5 p-0.5 rounded-control bg-raised">
      {THEME_OPTIONS.map(({ value, label, icon: Icon }) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={theme === value}
          onClick={() => setTheme(value)}
          className={`flex-1 inline-flex items-center justify-center gap-1.5 h-7 rounded-[5px] text-small transition-colors ${
            theme === value ? 'bg-surface text-ink font-medium' : 'text-muted hover:text-ink'
          }`}
        >
          <Icon className="w-3.5 h-3.5" strokeWidth={1.6} aria-hidden="true" />
          {label}
        </button>
      ))}
    </div>
  );
}

const NAV = [
  { page: 'chat', label: 'Chat', icon: MessageSquare },
  { page: 'documents', label: 'Documents', icon: FileText },
  { page: 'overview', label: 'Overview', icon: BarChart3 },
];

function NavLink({ href, active, icon: Icon, children, onNavigate }) {
  return (
    <a
      href={href}
      onClick={onNavigate}
      aria-current={active ? 'page' : undefined}
      className={`flex items-center gap-2.5 h-8 px-2 rounded-control text-body transition-colors ${
        active ? 'bg-active text-ink' : 'text-muted hover:text-ink hover:bg-raised'
      }`}
    >
      <Icon className="w-4 h-4 flex-none" strokeWidth={1.6} aria-hidden="true" />
      <span className="truncate">{children}</span>
    </a>
  );
}

function ChatRow({ conv, active, answering, onDelete, onNavigate }) {
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <div className="flex items-center gap-2 h-8 px-2 rounded-control bg-raised text-body">
        <span className="truncate flex-1">Delete this chat?</span>
        <button className="font-medium text-negative hover:underline" onClick={() => onDelete(conv.id)}>Delete</button>
        <button className="text-muted hover:text-ink" onClick={() => setConfirming(false)}>Cancel</button>
      </div>
    );
  }

  return (
    <div className="group relative">
      <a
        href={hrefFor('chat', conv.id)}
        onClick={onNavigate}
        aria-current={active ? 'page' : undefined}
        title={conv.title}
        className={`flex items-center h-8 pl-2 pr-8 rounded-control text-body transition-colors ${
          active ? 'bg-active text-ink' : 'text-[color:var(--text-2)] hover:bg-raised'
        }`}
      >
        <span className="truncate">{conv.title}</span>
        {answering && <span className="ml-2 flex-none text-small text-muted">answering…</span>}
      </a>
      <button
        onClick={() => setConfirming(true)}
        aria-label={`Delete chat "${conv.title}"`}
        className="absolute right-1 top-1 w-6 h-6 grid place-items-center rounded text-muted hover:text-ink hover:bg-active opacity-0 group-hover:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity"
      >
        <Trash2 className="w-3.5 h-3.5" strokeWidth={1.6} />
      </button>
    </div>
  );
}

export default function Sidebar({
  user, route, conversations, documentsCount, systemOnline, onDeleteConversation, onLogout, onNavigate,
}) {
  const streaming = useStreamingKeys();
  const groups = groupByRecency(conversations);

  return (
    <div className="flex flex-col h-full px-3 pt-4 pb-3 gap-1">
      <div className="flex items-center gap-2.5 px-2 h-9">
        <Mark size={22} />
        <span className="text-section font-medium">Agentic RAG</span>
      </div>
      <p className="px-2 text-small text-muted mb-3">
        {documentsCount == null
          ? 'Research assistant'
          : `${documentsCount} document${documentsCount === 1 ? '' : 's'} to answer from`}
      </p>

      <a href={hrefFor('chat')} onClick={onNavigate} className="btn-secondary w-full mb-3">
        <Plus className="w-4 h-4" strokeWidth={1.8} aria-hidden="true" />
        New chat
      </a>

      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map((item) => (
          <NavLink
            key={item.page}
            href={hrefFor(item.page)}
            icon={item.icon}
            active={route.page === item.page && !(item.page === 'chat' && route.conversationId)}
            onNavigate={onNavigate}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>

      <div className="flex-1 min-h-0 overflow-y-auto -mx-1 px-1 mt-6" aria-label="Chats">
        {conversations.length === 0 && (
          <p className="px-2 text-small text-muted">Your chats will be listed here.</p>
        )}
        {groups.map((group) => (
          <section key={group.label} className="mb-5">
            <h2 className="px-2 mb-1.5 text-small text-muted">{group.label}</h2>
            <div className="flex flex-col gap-0.5">
              {group.items.map((conv) => (
                <ChatRow
                  key={conv.id}
                  conv={conv}
                  active={route.page === 'chat' && route.conversationId === conv.id}
                  answering={streaming.includes(conv.id)}
                  onDelete={onDeleteConversation}
                  onNavigate={onNavigate}
                />
              ))}
            </div>
          </section>
        ))}
      </div>

      <div className="pt-3 px-1"><ThemeSwitch /></div>

      <div className="pt-3 flex items-center gap-2.5 px-2">
        <span
          className="w-7 h-7 flex-none rounded-full grid place-items-center text-small font-medium bg-[var(--avatar-bg)] text-[color:var(--avatar-ink)]"
          aria-hidden="true"
        >
          {(user?.username || '?').slice(0, 1).toUpperCase()}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-body truncate">{user?.username}</p>
          <p className={`text-small ${systemOnline ? 'text-muted' : 'text-negative'}`}>
            {systemOnline ? 'Connected' : 'Server offline'}
          </p>
        </div>
        <button onClick={onLogout} className="btn-ghost w-8 h-8 px-0" aria-label="Log out" title="Log out">
          <LogOut className="w-4 h-4" strokeWidth={1.6} />
        </button>
      </div>
    </div>
  );
}
