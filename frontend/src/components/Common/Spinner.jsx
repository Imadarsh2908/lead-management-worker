import React from 'react';

export default function Spinner({ size = 32, color = 'hsl(263 90% 64%)' }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      style={{ animation: 'spin 0.8s linear infinite', display: 'block' }}
    >
      <style>{`@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
      <circle cx="16" cy="16" r="13" stroke="rgba(255,255,255,0.08)" strokeWidth="3" />
      <path
        d="M16 3 A13 13 0 0 1 29 16"
        stroke={color}
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}
