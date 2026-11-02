// Hash routes, so the back button, refresh and links work without a server-side router:
//   #/chat (new chat) · #/chat/<id> · #/documents · #/overview
import { useEffect, useState } from 'react';

export function parseHash(hash = window.location.hash) {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean);
  if (parts[0] === 'documents') return { page: 'documents' };
  if (parts[0] === 'overview') return { page: 'overview' };
  return { page: 'chat', conversationId: parts[0] === 'chat' ? parts[1] || null : null };
}

export const hrefFor = (page, conversationId) =>
  page === 'chat' ? (conversationId ? `#/chat/${conversationId}` : '#/chat') : `#/${page}`;

export function navigate(page, conversationId, { replace = false } = {}) {
  const href = hrefFor(page, conversationId);
  if (window.location.hash === href) return;
  if (replace) window.history.replaceState(null, '', href);
  else window.history.pushState(null, '', href);
  window.dispatchEvent(new HashChangeEvent('hashchange'));
}

export function useRoute() {
  const [route, setRoute] = useState(() => parseHash());
  useEffect(() => {
    const onChange = () => setRoute(parseHash());
    window.addEventListener('hashchange', onChange);
    window.addEventListener('popstate', onChange);
    return () => {
      window.removeEventListener('hashchange', onChange);
      window.removeEventListener('popstate', onChange);
    };
  }, []);
  return route;
}
