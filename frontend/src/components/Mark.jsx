import React from 'react';

// The product's mark: a citation, two brackets around the one hue. Every answer shows where it came from.
export default function Mark({ size = 22, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" className={className}>
      <path d="M8 4H5v16h3" stroke="var(--text)" strokeWidth="2.2" strokeLinecap="square" />
      <path d="M16 4h3v16h-3" stroke="var(--text)" strokeWidth="2.2" strokeLinecap="square" />
      <circle cx="12" cy="12" r="2.6" fill="var(--hue)" />
    </svg>
  );
}
