import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, Menu, Plus, X } from 'lucide-react';
import Sidebar from './components/Sidebar';
import Login from './pages/Login';
import Chat from './pages/Chat';
import Documents from './pages/Documents';
import Overview from './pages/Overview';
import { apiService, auth, setUnauthorizedHandler } from './services/api';
import { hrefFor, navigate, useRoute } from './lib/route';
import { forgetConversation } from './state/chatStore';

const PAGE_TITLES = { chat: 'Chat', documents: 'Documents', overview: 'Overview' };

export default function App() {
  const [user, setUser] = useState(null);
  const [checkingSession, setCheckingSession] = useState(Boolean(auth.getToken()));
  const [systemOnline, setSystemOnline] = useState(false);
  const [conversations, setConversations] = useState([]);
  const [documents, setDocuments] = useState(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const route = useRoute();

  // Keep checking the backend so the app recovers on its own, whichever server started first.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    let cancelled = false;
    let timer;
    const check = async () => {
      let online = false;
      try {
        await apiService.checkHealth();
        online = true;
      } catch { /* backend not up (yet) */ }
      if (cancelled) return;
      setSystemOnline(online);
      timer = setTimeout(check, online ? 15000 : 4000);
    };
    check();
    return () => { cancelled = true; clearTimeout(timer); };
  }, []);

  // Restore the saved session once the backend is reachable. A backend that is merely down must not log
  // the user out: only a real 401 clears the token (handled in api.jsx).
  useEffect(() => {
    if (!systemOnline) return;
    if (user || !auth.getToken()) {
      setCheckingSession(false);
      return;
    }
    let cancelled = false;
    apiService.me()
      .then((u) => { if (!cancelled) setUser(u); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setCheckingSession(false); });
    return () => { cancelled = true; };
  }, [systemOnline]); // eslint-disable-line react-hooks/exhaustive-deps

  const refreshConversations = useCallback(async () => {
    try { setConversations(await apiService.getConversations()); } catch { /* shown on the next refresh */ }
  }, []);

  const refreshDocuments = useCallback(async () => {
    try { setDocuments(await apiService.getDocuments()); } catch { /* the Documents page shows its own error */ }
  }, []);

  useEffect(() => {
    if (user) {
      refreshConversations();
      refreshDocuments();
    }
  }, [user, refreshConversations, refreshDocuments]);

  // The phone menu closes whenever the route changes.
  useEffect(() => { setMenuOpen(false); }, [route.page, route.conversationId]);

  const handleLogout = () => {
    apiService.logout();
    setUser(null);
    setConversations([]);
    setDocuments(null);
    navigate('chat', null, { replace: true });
  };

  const handleDeleteConversation = async (id) => {
    await apiService.deleteConversation(id);
    forgetConversation(id);
    if (route.conversationId === id) navigate('chat', null, { replace: true });
    refreshConversations();
  };

  if (checkingSession) {
    return (
      <div className="h-full grid place-items-center text-muted">
        <div className="flex items-center gap-2 text-body">
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
          {systemOnline ? 'Restoring your session…' : 'Waiting for the server. This page connects by itself once it starts.'}
        </div>
      </div>
    );
  }

  if (!user) return <Login onAuthenticated={setUser} systemOnline={systemOnline} />;

  const indexed = documents?.filter((d) => d.status === 'indexed') ?? null;
  const activeTitle = route.page === 'chat'
    ? conversations.find((c) => c.id === route.conversationId)?.title || 'New chat'
    : PAGE_TITLES[route.page];

  const sidebar = (
    <Sidebar
      user={user}
      route={route}
      conversations={conversations}
      documentsCount={indexed?.length ?? null}
      systemOnline={systemOnline}
      onDeleteConversation={handleDeleteConversation}
      onLogout={handleLogout}
      onNavigate={() => setMenuOpen(false)}
    />
  );

  return (
    <div className="h-full flex flex-col lg:grid lg:grid-cols-[var(--sidebar-width)_minmax(0,1fr)]">
      <aside className="hidden lg:block h-full min-h-0 border-r border-line bg-ground">{sidebar}</aside>

      {/* Phone and tablet: the sidebar folds into a 48px bar with the screen's name and one action. */}
      <header className="lg:hidden h-12 flex-none flex items-center gap-2 px-2 border-b border-line bg-ground">
        <button className="btn-ghost w-10 h-10 px-0" onClick={() => setMenuOpen(true)} aria-label="Open menu">
          <Menu className="w-5 h-5" strokeWidth={1.6} />
        </button>
        <span className="flex-1 truncate text-section font-medium">{activeTitle}</span>
        <a href={hrefFor('chat')} className="btn-ghost w-10 h-10 px-0" aria-label="New chat">
          <Plus className="w-5 h-5" strokeWidth={1.6} />
        </a>
      </header>
      {menuOpen && (
        <div className="lg:hidden fixed inset-0 z-40 bg-ground flex flex-col" role="dialog" aria-modal="true" aria-label="Menu">
          <div className="h-12 flex-none flex items-center justify-end px-2">
            <button className="btn-ghost w-10 h-10 px-0" onClick={() => setMenuOpen(false)} aria-label="Close menu">
              <X className="w-5 h-5" strokeWidth={1.6} />
            </button>
          </div>
          <div className="flex-1 min-h-0">{sidebar}</div>
        </div>
      )}

      <main className="flex-1 min-h-0 min-w-0 h-full">
        {route.page === 'chat' && (
          <Chat
            key={route.conversationId || 'new'}
            conversationId={route.conversationId}
            title={activeTitle}
            documents={indexed}
            onConversationCreated={(id) => {
              // Only follow the new chat if the user is still looking at it.
              if (!window.location.hash || window.location.hash === '#/chat' || window.location.hash === '#/') {
                navigate('chat', id, { replace: true });
              }
              refreshConversations();
            }}
            onAnswered={refreshConversations}
            onDelete={handleDeleteConversation}
          />
        )}
        {route.page === 'documents' && <Documents onChanged={refreshDocuments} />}
        {route.page === 'overview' && <Overview documents={documents} />}
      </main>
    </div>
  );
}
