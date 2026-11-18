import React from 'react';
import { usageTone } from './UsageBar';
import { formatCount } from '../../lib/format';

const windowLabel = (seconds) => (seconds >= 86400 ? 'Messages, 24 h' : `Messages, ${Math.round(seconds / 60)} min`);

function Cell({ used, limit }) {
  return (
    <td className="text-right num" style={limit && used / limit >= 0.8 ? { color: usageTone(used, limit) } : undefined}>
      {formatCount(used)}{limit ? <span className="text-muted"> / {formatCount(limit)}</span> : null}
    </td>
  );
}

// Counts per account against its allowances. Counts only: no message content, no document titles.
export default function AccountsTable({ accounts }) {
  return (
    <section className="panel p-5 min-w-0">
      <h2 className="text-section font-medium">Accounts</h2>
      <p className="text-body text-muted">
        {accounts.rows.length
          ? `${accounts.rows.length} of ${accounts.total} accounts used the chat or indexing recently.`
          : `None of the ${accounts.total} accounts used the chat or indexing recently.`}
        {' '}Chat counts are kept in memory, so a restart clears them.
      </p>
      {accounts.rows.length > 0 && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-body">
            <thead>
              <tr className="text-small text-muted">
                <th className="text-left font-normal h-9">Account</th>
                {accounts.chat_windows.map((w) => (
                  <th key={w.seconds} className="text-right font-normal">{windowLabel(w.seconds)}</th>
                ))}
                <th className="text-right font-normal">Passages indexed today</th>
              </tr>
            </thead>
            <tbody>
              {accounts.rows.map((r) => (
                <tr key={r.username} className="border-t border-rule">
                  <td className="py-2.5 pr-3 truncate max-w-[12rem]">{r.username}</td>
                  {accounts.chat_windows.map((w, i) => <Cell key={w.seconds} used={r.chat[i] || 0} limit={w.allowed} />)}
                  <Cell used={r.embedded_today} limit={accounts.embed_per_user} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
