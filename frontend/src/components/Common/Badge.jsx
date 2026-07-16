import React from 'react';

const variantMap = {
  HIGH:        { cls: 'badge-high',       label: 'HIGH' },
  MEDIUM:      { cls: 'badge-medium',     label: 'MEDIUM' },
  LOW:         { cls: 'badge-low',        label: 'LOW' },
  SPAM:        { cls: 'badge-unassigned', label: 'SPAM' },
  UNASSIGNED:  { cls: 'badge-unassigned', label: 'UNASSIGNED' },

  RECEIVED:    { cls: 'badge-info',       label: 'RECEIVED' },
  VALIDATING:  { cls: 'badge-info',       label: 'VALIDATING' },
  ENRICHING:   { cls: 'badge-info',       label: 'ENRICHING' },
  ANALYZING:   { cls: 'badge-info',       label: 'ANALYZING' },
  EXECUTING:   { cls: 'badge-info',       label: 'EXECUTING' },
  COMPLETED:   { cls: 'badge-low',        label: 'COMPLETED' },
  ESCALATED:   { cls: 'badge-medium',     label: 'ESCALATED' },
  FAILED:      { cls: 'badge-high',       label: 'FAILED' },

  STATE_TRANSITION: { cls: 'badge-info',    label: 'TRANSITION' },
  TOOL_INVOCATION:  { cls: 'badge-medium',  label: 'TOOL' },
  LLM_REASONING:    { cls: 'badge-purple',  label: 'LLM' },
  ESCALATION:       { cls: 'badge-high',    label: 'ESCALATION' },
  SYSTEM_ERROR:     { cls: 'badge-high',    label: 'ERROR' },
  MANUAL_OVERRIDE:  { cls: 'badge-info',    label: 'MANUAL' },

  ACTIVE:           { cls: 'badge-low',     label: 'ACTIVE' },
  REVOKED:          { cls: 'badge-high',    label: 'REVOKED' },
};

export default function Badge({ value }) {
  const key = String(value).toUpperCase();
  const { cls, label } = variantMap[key] || { cls: 'badge-unassigned', label: key };
  return <span className={`badge ${cls}`}>{label}</span>;
}
