const nf = new Intl.NumberFormat('en-US');

export const formatCount = (n) => (n == null ? '—' : nf.format(n));

export const formatSize = (bytes) => {
  if (bytes == null) return '—';
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
};

export const formatSeconds = (ms) => (ms ? `${(ms / 1000).toFixed(1)} s` : '—');

/** A share as a whole percentage, or a dash when there is nothing to divide. */
export const formatShare = (n, total) => (total ? `${Math.round((n / total) * 100)}%` : '—');

const dayMs = 24 * 60 * 60 * 1000;
const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/** "Today", "Yesterday", "Oct 24" or "Oct 24, 2025": recency in words. */
export function formatDay(iso, now = new Date()) {
  if (!iso) return '—';
  const d = new Date(iso);
  const days = Math.round((startOfDay(now) - startOfDay(d)) / dayMs);
  if (days === 0) return 'Today';
  if (days === 1) return 'Yesterday';
  const opts = d.getFullYear() === now.getFullYear()
    ? { month: 'short', day: 'numeric' }
    : { month: 'short', day: 'numeric', year: 'numeric' };
  return d.toLocaleDateString('en-US', opts);
}

/** Group conversations for the sidebar: Today, Previous 7 days, Older. */
export function groupByRecency(conversations, now = new Date()) {
  const groups = [
    { label: 'Today', items: [] },
    { label: 'Previous 7 days', items: [] },
    { label: 'Older', items: [] },
  ];
  for (const c of conversations) {
    const days = Math.round((startOfDay(now) - startOfDay(new Date(c.updated_at))) / dayMs);
    groups[days <= 0 ? 0 : days <= 7 ? 1 : 2].items.push(c);
  }
  return groups.filter((g) => g.items.length);
}

/** Where an answer came from, in the product's words. */
export const SOURCE_LABELS = {
  rag: 'your documents',
  web_search: 'a web search',
  direct: "the model's own knowledge",
  greeting: 'small talk',
  no_results: 'no source',
};
