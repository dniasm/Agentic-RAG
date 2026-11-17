import React from 'react';
import UsageBar from './UsageBar';
import { formatAgo, formatCount } from '../../lib/format';

const shortModel = (model) => model.split('/').pop();
const num = (v) => (v == null || v === '' ? null : Number(v));

// What Groq said about one model on this key, with its last response: its own count of what is left.
function GroqSays({ headers }) {
  if (!headers) return <p className="text-small text-muted">No reply from Groq seen since the server started.</p>;
  const reqLimit = num(headers.limit_requests);
  const tokLimit = num(headers.limit_tokens);
  return (
    <div className="flex flex-col gap-2.5">
      {reqLimit != null && (
        <UsageBar label="Requests used (Groq's count)" used={reqLimit - (num(headers.remaining_requests) ?? reqLimit)}
                  limit={reqLimit} note={headers.reset_requests ? `resets in ${headers.reset_requests}` : null} />
      )}
      {tokLimit != null && (
        <UsageBar label="Tokens used (Groq's count)" used={tokLimit - (num(headers.remaining_tokens) ?? tokLimit)}
                  limit={tokLimit} note={headers.reset_tokens ? `resets in ${headers.reset_tokens}` : null} />
      )}
      <p className="text-small text-muted">As of Groq's last reply, {formatAgo(headers.seconds_ago)}.</p>
    </div>
  );
}

function Counts({ label, c }) {
  return (
    <p className="flex items-baseline justify-between gap-3 text-small">
      <span className="text-muted">{label}</span>
      <span className="num">
        {formatCount(c.calls)} calls · {formatCount(c.tokens)} tokens
        {c.limited ? <span className="text-[var(--warning)]"> · {formatCount(c.limited)} refused</span> : null}
      </span>
    </p>
  );
}

export default function KeyCard({ entry }) {
  return (
    <section className="panel p-5 flex flex-col gap-4 min-w-0">
      <div>
        <h3 className="text-section font-medium">Groq key {entry.key}</h3>
        <p className="text-body text-muted num">
          {formatCount(entry.last_24h.calls)} calls · {formatCount(entry.last_24h.tokens)} tokens in the last 24 hours
        </p>
      </div>
      {entry.models.length === 0 && <p className="text-body text-muted">Not used yet.</p>}
      {entry.models.map((m) => (
        <div key={m.model} className="border-t border-rule pt-3 flex flex-col gap-2.5">
          <p className="flex items-center justify-between gap-3">
            <span className="text-body font-medium truncate" title={m.model}>{shortModel(m.model)}</span>
            {m.cooldown_seconds ? (
              <span className="text-small text-[var(--warning)] flex-none">
                rate-limited, back in {Math.ceil(m.cooldown_seconds)} s
              </span>
            ) : null}
          </p>
          <GroqSays headers={m.headers} />
          <div className="flex flex-col gap-1">
            <Counts label="Today (UTC)" c={m.today} />
            <Counts label="Last 24 hours" c={m.last_24h} />
            <Counts label="Last 7 days" c={m.last_7d} />
          </div>
        </div>
      ))}
    </section>
  );
}
