/** The tab's one WebSocket to the backend (GET /api/ws): everything the server pushes arrives here. Framework-free
    (the browser's WebSocket and timers are passed in, so node can test it: backend/tests/test_frontend_logic.py).

    - Auth is the login cookie the browser sends on the handshake. Closed with 4401 (not signed in) or 1008 (refused):
      no retry; connect() again after signing in.
    - Where the page's host can't carry the socket (Vercel), makeUrl returns a Promise of the URL (it fetches a one-time
      ticket over REST first, services/realtime.js), called before EVERY connect and reconnect. A failure with
      status 401 there means not signed in (no retry); any other failure retries with the usual backoff.
    - Reconnects by itself with a growing, jittered delay. It remembers the last event's seq and asks for everything
      after it (`?after=`); the server answers `resync` when it can't serve that, and listeners get the message and
      read their state over REST.
    - A ping goes out every PING_MS; a socket that has been silent for DEAD_MS (the server sends a heartbeat every
      20 s) is closed and reconnected.
    - Listeners receive each envelope { seq, channel, type, request_id, data, ts }, including system/hello and
      system/resync, and connection-state changes ('connecting' | 'open' | 'closed' | 'denied'). */

export const PING_MS = 25000;
export const DEAD_MS = 55000;
export const DENIED_CODES = [4401, 1008];

/** Delay before reconnect attempt number `attempt` (0 = first): 0.5 s, 1 s, 2 s ... up to 30 s, +-25% jitter. */
export function backoffDelay(attempt, random = Math.random) {
  const base = Math.min(30000, 500 * 2 ** attempt);
  return Math.round(base * (0.75 + random() * 0.5));
}

/** ws(s)://host/api/ws from the API base ('/api' or an absolute URL) and the page's location, with ?after=seq. */
export function socketUrl(apiBase, loc, after = null) {
  const path = `${apiBase.replace(/\/$/, '')}/ws`;
  const abs = /^https?:\/\//.test(path)
    ? path.replace(/^http/, 'ws')
    : `${loc.protocol === 'https:' ? 'wss' : 'ws'}://${loc.host}${path}`;
  return after == null ? abs : `${abs}?after=${after}`;
}

/** The URL of a ticketed socket: `base` (an absolute ws(s) URL, e.g. VITE_WS_URL) + ?ticket=...[&after=seq]. */
export function ticketUrl(base, ticket, after = null) {
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}ticket=${encodeURIComponent(ticket)}${after == null ? '' : `&after=${after}`}`;
}

export class RealtimeSocket {
  // timers default to globalThis itself: a browser throws "Illegal invocation" when its clearTimeout etc. are
  // called as methods of any other object, which blanked the whole page on mount.
  constructor({ makeUrl, WS, timers = globalThis, random = Math.random }) {
    this.makeUrl = makeUrl;
    this.WS = WS;
    this.t = timers;
    this.random = random;
    this.listeners = new Set();
    this.stateListeners = new Set();
    this.state = 'closed';
    this.wanted = false;
    this.attempt = 0;
    this.lastSeq = null;
    this.helloSeq = null;
    this.ws = null;
    this.retry = null;
    this.opening = false;
    this.watch = null;
    this.lastHeard = 0;
    this.now = () => Date.now();
  }

  /** Start (or keep) the connection. Safe to call again. */
  connect() {
    this.wanted = true;
    if (this.ws || this.retry || this.opening) return;
    this._open();
  }

  /** Sign-out: close for good and forget the cursor (the next account starts fresh). */
  close() {
    this.wanted = false;
    this.lastSeq = null;
    this.t.clearTimeout(this.retry);
    this.retry = null;
    this.opening = false;   // a URL still being fetched is dropped when it arrives
    this._stopWatch();
    const ws = this.ws;
    this.ws = null;
    if (ws) { ws.onclose = null; ws.onmessage = null; try { ws.close(1000); } catch { /* already closed */ } }
    this._setState('closed');
  }

  isOpen() { return this.state === 'open'; }

  subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }

  onState(fn) { this.stateListeners.add(fn); return () => this.stateListeners.delete(fn); }

  /** Resolves true once the server has said hello, false after `ms` (the caller goes on without the socket). */
  ready(ms = 3000) {
    if (this.isOpen()) return Promise.resolve(true);
    this.connect();
    return new Promise((resolve) => {
      const timer = this.t.setTimeout(() => { off(); resolve(false); }, ms);
      const off = this.onState((s) => {
        if (s === 'open' || s === 'denied') { this.t.clearTimeout(timer); off(); resolve(s === 'open'); }
      });
    });
  }

  _setState(s) {
    if (this.state === s) return;
    this.state = s;
    this.stateListeners.forEach((fn) => fn(s));
  }

  _open() {
    this._setState('connecting');
    let url;
    try { url = this.makeUrl(this.lastSeq); } catch { this._scheduleRetry(); return; }
    if (typeof url?.then !== 'function') { this._start(url); return; }
    const mine = this.opening = {};
    url.then((u) => {
      if (this.opening !== mine) return;   // closed meanwhile
      this.opening = false;
      this._start(u);
    }, (err) => {
      if (this.opening !== mine) return;
      this.opening = false;
      if (err?.status === 401) { this.wanted = false; this._setState('denied'); return; }
      this._setState('closed');
      if (this.wanted) this._scheduleRetry();
    });
  }

  _start(url) {
    let ws;
    try { ws = new this.WS(url); } catch { this._scheduleRetry(); return; }
    this.ws = ws;
    this.lastHeard = this.now();
    ws.onmessage = (e) => this._onMessage(e.data);
    ws.onclose = (e) => this._onClose(ws, e);
    ws.onerror = () => { /* onclose follows */ };
    this._startWatch(ws);
  }

  _onMessage(raw) {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }
    this.lastHeard = this.now();
    if (msg.channel === 'system' && msg.type === 'hello') {
      this.helloSeq = msg.data.seq;
      if (this.lastSeq == null) this.lastSeq = msg.data.seq;
      this.attempt = 0;
      this._setState('open');
    } else if (msg.seq > 0 && (this.lastSeq == null || msg.seq > this.lastSeq)) {
      this.lastSeq = msg.seq;
    }
    if (msg.channel === 'system' && msg.type === 'resync') this.lastSeq = this.helloSeq;   // what couldn't be replayed is read over REST: carry on from the latest
    this.listeners.forEach((fn) => { try { fn(msg); } catch (err) { console.error(err); } });
  }

  _onClose(ws, event) {
    if (ws !== this.ws) return;
    this.ws = null;
    this._stopWatch();
    if (DENIED_CODES.includes(event?.code)) { this.wanted = false; this._setState('denied'); return; }
    this._setState('closed');
    if (this.wanted) this._scheduleRetry();
  }

  _scheduleRetry() {
    this.t.clearTimeout(this.retry);
    this.retry = this.t.setTimeout(() => { this.retry = null; if (this.wanted) this._open(); },
      backoffDelay(this.attempt++, this.random));
  }

  _startWatch(ws) {
    this._stopWatch();
    this.watch = this.t.setInterval(() => {
      if (this.now() - this.lastHeard > DEAD_MS) { try { ws.close(); } catch { /* gone */ } this._onClose(ws, { code: 1006 }); return; }
      if (ws.readyState === 1) { try { ws.send(JSON.stringify({ type: 'ping' })); } catch { /* closing */ } }
    }, PING_MS);
  }

  _stopWatch() { this.t.clearInterval(this.watch); this.watch = null; }
}
