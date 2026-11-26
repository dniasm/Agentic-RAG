// The tab's one WebSocket (lib/realtimeSocket.js): App.jsx connects it after sign-in and closes it on sign-out.
import { RealtimeSocket, socketUrl } from '../lib/realtimeSocket';
import { API_BASE_URL } from './config';

export const realtime = new RealtimeSocket({
  makeUrl: (after) => socketUrl(API_BASE_URL, window.location, after),
  WS: globalThis.WebSocket,
});
