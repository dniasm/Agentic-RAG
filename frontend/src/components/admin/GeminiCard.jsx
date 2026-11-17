import React from 'react';
import UsageBar from './UsageBar';
import { formatCount } from '../../lib/format';

function keyNote(k) {
  if (k.rejected) return 'Rejected by Google (mistyped or revoked?): skipped for an hour at a time.';
  if (k.out_for_day) return 'Refused by Google for the rest of its day.';
  if (k.cooldown_seconds) return `Rate-limited, back in ${Math.ceil(k.cooldown_seconds)} s.`;
  return null;
}

// One Gemini key: its requests today against EMBED_PER_KEY_DAY, and whether Google has refused it.
function GeminiKey({ k, perKey }) {
  const used = k.documents + k.questions;
  return (
    <div className="border-t border-rule pt-3 flex flex-col gap-1.5">
      <UsageBar label={`Key ${k.key}`} used={used} limit={perKey || null} note={keyNote(k)} />
      <p className="text-small text-muted num">
        {formatCount(k.documents)} for documents · {formatCount(k.questions)} for questions
        {k.limited ? <span className="text-[var(--warning)]"> · {formatCount(k.limited)} refused</span> : null}
      </p>
      {k.shares_quota && (
        <p className="text-small text-[var(--warning)]">
          Refused for the day well under its count: this key probably shares a Google project with another key, so it
          adds no quota.
        </p>
      )}
    </div>
  );
}

export default function GeminiCard({ g }) {
  return (
    <section className="panel p-5 flex flex-col gap-4 min-w-0">
      <div>
        <h3 className="text-section font-medium">Gemini embeddings</h3>
        <p className="text-body text-muted">
          Our own count: Google doesn't report what's left. Its day runs on Pacific time ({g.day}).
          {g.fixed_total ? ' The day is a fixed total (EMBED_SITE_PER_DAY).' : ' The day is what the keys have left.'}
        </p>
      </div>
      <UsageBar label="Requests today, all keys" used={g.total} limit={g.site_limit} />
      <UsageBar label="Passages indexed (documents)" used={g.indexed_charged} limit={g.index_limit}
                note="Indexing stops here, so the rest is kept for questions." />
      <p className="text-small text-muted num">
        {formatCount(g.documents)} for documents · {formatCount(g.questions)} for questions (repeat questions are cached)
      </p>
      {g.keys.length > 1 && g.keys.map((k) => <GeminiKey key={k.key} k={k} perKey={g.per_key} />)}
    </section>
  );
}
