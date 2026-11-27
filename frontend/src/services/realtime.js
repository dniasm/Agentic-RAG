// The tab's one WebSocket (lib/realtimeSocket.js): App.jsx connects it after sign-in and closes it on sign-out.
// Same-origin by default (the login cookie goes with the handshake). With VITE_WS_URL set (e.g.
// wss://app.onrender.com/api/ws, when the page's host proxies REST but not WebSockets) a one-time ticket is fetched
// over REST before every (re)connect and sent as ?ticket=.
import { RealtimeSocket, socketUrl, ticketUrl } from '../lib/realtimeSocket';
import { apiService } from './api';
import { API_BASE_URL } from './config';

const WS_URL = import.meta.env.VITE_WS_URL || '';

export const realtime = new RealtimeSocket({
  makeUrl: WS_URL
    ? async (after) => ticketUrl(WS_URL, (await apiService.wsTicket()).ticket, after)
    : (after) => socketUrl(API_BASE_URL, window.location, after),
  WS: globalThis.WebSocket,
});
