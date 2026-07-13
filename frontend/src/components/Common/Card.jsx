import React from 'react';

export default function Card({ children, className = '', hoverable = false, style = {} }) {
  return (
    <div
      className={`glass-panel ${hoverable ? 'hoverable' : ''} ${className}`}
      style={style}
    >
      {children}
    </div>
  );
}
