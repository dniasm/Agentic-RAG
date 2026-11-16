/** A "⋯" button that opens a short menu of actions: one control in place of a row of links. Closes on a choice, a
    click elsewhere, Escape or Tab; arrow keys move between the items. */
import React, { useEffect, useRef, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';

/** items: [{ label, onSelect?, href?, download?, danger?, separatorBefore? }] (falsy entries are skipped).
    label: what the button is for, for screen readers ("Actions for Report.pdf"). */
export default function ActionMenu({ items, label }) {
  const shown = items.filter(Boolean);
  const [open, setOpen] = useState(false);
  const root = useRef(null);
  const button = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const away = (e) => { if (!root.current?.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', away);
    root.current?.querySelector('[role="menuitem"]')?.focus();
    return () => document.removeEventListener('mousedown', away);
  }, [open]);

  const close = (refocus = true) => { setOpen(false); if (refocus) button.current?.focus(); };
  const onKey = (e) => {
    const entries = [...(root.current?.querySelectorAll('[role="menuitem"]') || [])];
    const at = entries.indexOf(document.activeElement);
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'Tab') close(false);
    else if (e.key === 'ArrowDown') { e.preventDefault(); entries[(at + 1) % entries.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); entries[(at - 1 + entries.length) % entries.length]?.focus(); }
  };

  const itemClass = (item) => `block w-full text-left px-3 py-2 text-body rounded-control hover:bg-raised focus:bg-raised outline-none ${
    item.danger ? 'text-negative' : 'text-ink'}`;

  return (
    <span ref={root} className="relative inline-block" onKeyDown={onKey}>
      <button ref={button} type="button" className="btn-ghost h-8 w-8 p-0" aria-haspopup="menu" aria-expanded={open}
              aria-label={label} title="More actions" onClick={() => setOpen((o) => !o)}>
        <MoreHorizontal className="w-4 h-4" aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" aria-label={label}
             className="absolute right-0 top-full mt-1 z-20 min-w-[200px] p-1 panel shadow-lg text-left">
          {shown.map((item, i) => (
            <React.Fragment key={item.label}>
              {item.separatorBefore && i > 0 && <div role="separator" className="my-1 border-t border-rule" />}
              {item.href ? (
                <a role="menuitem" tabIndex={-1} className={itemClass(item)} href={item.href}
                   download={item.download || undefined} onClick={() => close(false)}>
                  {item.label}
                </a>
              ) : (
                <button role="menuitem" tabIndex={-1} type="button" className={itemClass(item)}
                        onClick={() => { close(false); item.onSelect?.(); }}>
                  {item.label}
                </button>
              )}
            </React.Fragment>
          ))}
        </div>
      )}
    </span>
  );
}
