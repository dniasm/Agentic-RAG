import React, { useState } from 'react';
import { Loader2 } from 'lucide-react';
import Mark from '../components/Mark';
import { apiService } from '../services/api';

// What an answer looks like: the product's one idea (every answer says where it came from), shown as a real
// fragment of the chat rather than described. Sample content from the benchmark corpus.
function ExampleAnswer() {
  return (
    <div data-ui className="panel p-6 max-w-[520px] w-full">
      <p className="text-small text-muted mb-4">Example answer</p>
      <div className="flex justify-end mb-5">
        <p className="bg-raised rounded-card px-3.5 py-2.5 text-reading max-w-[85%]">
          Which tech stack was used to build the complaint classification system?
        </p>
      </div>
      <p className="text-reading">
        The system is written in Python, with Flask serving the web application and MySQL storing the
        complaints and their categories <span className="cite-mark">[1]</span>. Each complaint is turned into
        TF-IDF features before a linear SVM assigns its category <span className="cite-mark">[2]</span>.
      </p>
      <p className="mt-4 text-small text-muted flex flex-wrap gap-x-2 gap-y-1">
        <span className="font-medium text-hue">From your documents</span>
        <span>CCS_using_SVM.pdf, p. 15 and p. 19</span>
        <span aria-hidden="true">·</span>
        <span>Checked 5/5</span>
        <span aria-hidden="true">·</span>
        <span className="num">3.4 s</span>
      </p>

      <div className="flex justify-end mt-8 mb-5">
        <p className="bg-raised rounded-card px-3.5 py-2.5 text-reading max-w-[85%]">
          Is Flask still a good choice for something like this?
        </p>
      </div>
      <p className="mb-3 rounded-control bg-raised px-3 py-2 text-body text-[color:var(--text-2)]">
        Your documents don't cover this, so it was answered with a web search.
      </p>
      <p className="text-reading">
        For a small internal tool, yes: Flask is actively maintained and 3.1 improved its async support
        <span className="cite-mark"> [1]</span>. FastAPI is the common pick for larger public APIs <span className="cite-mark">[2]</span>.
      </p>
      <p className="mt-4 text-small text-muted flex flex-wrap gap-x-2 gap-y-1">
        <span className="font-medium text-hue">From a web search</span>
        <span>flask.palletsprojects.com, fastapi.tiangolo.com</span>
        <span aria-hidden="true">·</span>
        <span>Checked 4/5</span>
      </p>
    </div>
  );
}

export default function Login({ onAuthenticated, systemOnline }) {
  const [mode, setMode] = useState('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const registering = mode === 'register';

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const user = registering
        ? await apiService.register(username.trim(), password)
        : await apiService.login(username.trim(), password);
      onAuthenticated(user);
    } catch (err) {
      setError(err.status === 0 ? 'Cannot reach the server. Check that the backend is running.' : err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-full grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
      <section className="flex flex-col px-6 sm:px-12 lg:px-16 py-10">
        <div className="flex items-center gap-2.5">
          <Mark size={24} />
          <span className="text-section font-medium">Agentic RAG</span>
        </div>

        <div className="flex-1 flex items-center">
          <div className="w-full max-w-[380px] py-12">
            <h1 className="text-title font-medium tracking-[-0.01em]">
              {registering ? 'Create your account' : 'Log in'}
            </h1>
            <p className="mt-2 text-body text-muted">
              A research assistant that answers from your documents, the web and its own knowledge, and tells you
              which one it used.
            </p>

            {!systemOnline && (
              <p className="mt-6 flex items-start gap-2 rounded-control px-3 py-2.5 text-body border border-[var(--notice-border)] bg-[var(--pill-warning-bg)] text-warning" role="status">
                <Loader2 className="w-4 h-4 mt-0.5 flex-none animate-spin" aria-hidden="true" />
                The server isn't running yet. This page connects by itself once it starts.
              </p>
            )}

            <form onSubmit={handleSubmit} className="mt-8">
              <label htmlFor="username" className="field-label">Username</label>
              <input
                id="username"
                className="input"
                required
                minLength={3}
                maxLength={32}
                pattern="[A-Za-z0-9_.\-]+"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                aria-describedby={registering ? 'username-rules' : undefined}
              />
              {registering && (
                <p id="username-rules" className="mt-1.5 text-small text-muted">
                  3–32 characters: letters, numbers, dots, dashes and underscores.
                </p>
              )}

              <label htmlFor="password" className="field-label mt-4">Password</label>
              <input
                id="password"
                type="password"
                className="input"
                required
                minLength={6}
                autoComplete={registering ? 'new-password' : 'current-password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-describedby={registering ? 'password-rules' : undefined}
              />
              {registering && (
                <p id="password-rules" className="mt-1.5 text-small text-muted">At least 6 characters.</p>
              )}

              {error && (
                <p className="mt-4 rounded-control px-3 py-2.5 text-body bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]" role="alert">
                  {error}
                </p>
              )}

              <button type="submit" disabled={busy || !systemOnline} className="btn-primary w-full h-10 mt-6">
                {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                {registering ? 'Create account' : 'Log in'}
              </button>
            </form>

            <p className="mt-6 text-body text-muted">
              {registering ? 'Already have an account?' : 'New here?'}{' '}
              <button
                type="button"
                className="font-medium text-ink underline-offset-4 hover:underline"
                onClick={() => { setMode(registering ? 'login' : 'register'); setError(null); }}
              >
                {registering ? 'Log in' : 'Create an account'}
              </button>
            </p>
          </div>
        </div>
      </section>

      <section className="hidden lg:flex items-center justify-center px-12 py-10 bg-raised">
        <ExampleAnswer />
      </section>
    </div>
  );
}
