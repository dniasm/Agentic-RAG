import React, { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import Chat from './pages/Chat';
import Documents from './pages/Documents';
import Dashboard from './pages/Dashboard';
import { apiService, auth, setUnauthorizedHandler } from './services/api';
import { Lock, Loader2, AlertCircle } from 'lucide-react';

function LoginScreen({ onAuthenticated, systemOnline }) {
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user = mode === 'login'
        ? await apiService.login(username.trim(), password)
        : await apiService.register(username.trim(), password);
      onAuthenticated(user);
    } catch (err) {
      setError(err.message === 'Failed to fetch' ? 'Cannot reach the backend server.' : err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-screen w-screen bg-slate-950 flex items-center justify-center p-4 text-slate-100">
      <div className="max-w-md w-full bg-slate-900 border border-slate-800 rounded-2xl p-8 shadow-2xl space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex p-3 bg-indigo-600/10 rounded-2xl text-indigo-400 border border-indigo-500/20">
            <Lock className="w-8 h-8" />
          </div>
          <h1 className="text-2xl font-bold text-white">Agentic RAG Access</h1>
          <p className="text-xs text-slate-400">
            {mode === 'login' ? 'Enter your credentials to access the system' : 'Create an account to get started'}
          </p>
        </div>

        {!systemOnline && (
          <div className="flex items-center space-x-2 text-xs text-amber-400 bg-amber-950/30 border border-amber-900/50 rounded-lg px-3 py-2">
            <Loader2 className="w-4 h-4 flex-shrink-0 animate-spin" />
            <span>Backend is offline. Start it and this page will connect automatically.</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="text-xs text-slate-400 font-medium block mb-1">Username</label>
            <input
              type="text"
              required
              minLength={3}
              maxLength={32}
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 focus:border-indigo-500 rounded-xl px-4 py-2.5 text-sm text-slate-200 outline-none"
              placeholder="admin"
            />
          </div>
          <div>
            <label className="text-xs text-slate-400 font-medium block mb-1">Password</label>
            <input
              type="password"
              required
              minLength={6}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 focus:border-indigo-500 rounded-xl px-4 py-2.5 text-sm text-slate-200 outline-none"
              placeholder="••••••••"
            />
          </div>

          {error && (
            <div className="flex items-center space-x-2 text-xs text-rose-400 bg-rose-950/40 border border-rose-900/50 rounded-lg px-3 py-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={busy}
            className="w-full bg-indigo-600 hover:bg-indigo-500 disabled:opacity-60 text-white font-medium py-2.5 rounded-xl transition shadow-lg shadow-indigo-600/20 flex items-center justify-center space-x-2"
          >
            {busy && <Loader2 className="w-4 h-4 animate-spin" />}
            <span>{mode === 'login' ? 'Log In' : 'Create Account'}</span>
          </button>
        </form>

        <p className="text-center text-xs text-slate-500">
          {mode === 'login' ? "Don't have an account? " : 'Already have an account? '}
          <button
            onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError(null); }}
            className="text-indigo-400 hover:text-indigo-300 font-medium"
          >
            {mode === 'login' ? 'Sign up' : 'Log in'}
          </button>
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(null);
  const [checkingSession, setCheckingSession] = useState(Boolean(auth.getToken()));

  const [activePage, setActivePage] = useState('chat');
  const [conversations, setConversations] = useState([]);
  const [activeConversationId, setActiveConversationId] = useState(null);
  const [systemOnline, setSystemOnline] = useState(false);

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

  // Restore the saved session once the backend is reachable. A backend that is merely down
  // must not log the user out: only a real 401 clears the token (handled in api.jsx).
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
    try {
      setConversations(await apiService.getConversations());
    } catch { /* surfaced elsewhere */ }
  }, []);

  useEffect(() => {
    if (user) refreshConversations();
  }, [user, refreshConversations]);

  const handleLogout = () => {
    apiService.logout();
    setUser(null);
    setConversations([]);
    setActiveConversationId(null);
    setActivePage('chat');
  };

  const handleDeleteConversation = async (id) => {
    await apiService.deleteConversation(id);
    if (id === activeConversationId) setActiveConversationId(null);
    refreshConversations();
  };

  if (checkingSession) {
    return (
      <div className="h-screen w-screen bg-slate-950 flex flex-col items-center justify-center space-y-3 text-slate-400">
        <Loader2 className="w-8 h-8 text-indigo-400 animate-spin" />
        {!systemOnline && (
          <p className="text-sm">Waiting for the backend server… it will connect automatically once it starts.</p>
        )}
      </div>
    );
  }

  if (!user) return <LoginScreen onAuthenticated={setUser} systemOnline={systemOnline} />;

  const activeTitle = activePage === 'chat'
    ? conversations.find((c) => c.id === activeConversationId)?.title || 'New Chat'
    : activePage === 'documents' ? 'Documents' : 'Dashboard';

  return (
    <div className="h-screen w-screen bg-slate-950 text-slate-100 flex overflow-hidden font-sans">
      <Sidebar
        user={user}
        activePage={activePage}
        setActivePage={setActivePage}
        conversations={conversations}
        activeConversationId={activeConversationId}
        onSelectConversation={(id) => { setActivePage('chat'); setActiveConversationId(id); }}
        onNewChat={() => { setActivePage('chat'); setActiveConversationId(null); }}
        onDeleteConversation={handleDeleteConversation}
        onLogout={handleLogout}
      />

      <div className="flex-1 flex flex-col h-full overflow-hidden">
        <header className="h-14 border-b border-slate-800 bg-slate-900/50 flex items-center justify-between px-6 select-none">
          <span className="font-semibold text-slate-200 truncate">{activeTitle}</span>
          <div className="flex items-center space-x-2">
            <span className={`w-2 h-2 rounded-full ${systemOnline ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'}`} />
            <span className="text-xs text-slate-400 font-medium">
              {systemOnline ? 'System Online' : 'Backend Offline'}
            </span>
          </div>
        </header>

        {activePage === 'chat' && (
          <Chat
            conversationId={activeConversationId}
            onConversationCreated={(id) => { setActiveConversationId(id); refreshConversations(); }}
            onMessageSent={refreshConversations}
            onGoToDocuments={() => setActivePage('documents')}
          />
        )}
        {activePage === 'documents' && <Documents />}
        {activePage === 'dashboard' && <Dashboard />}
      </div>
    </div>
  );
}
