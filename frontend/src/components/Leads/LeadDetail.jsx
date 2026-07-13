import React, { useEffect, useState, useRef } from 'react';
import { api } from '../../services/api';
import Badge from '../Common/Badge';
import Spinner from '../Common/Spinner';

const STATUS_FLOW = ['RECEIVED', 'VALIDATING', 'ENRICHING', 'ANALYZING', 'EXECUTING'];
const TERMINAL    = new Set(['COMPLETED', 'ESCALATED', 'FAILED']);

function stepState(stepKey, currentStatus) {
  if (currentStatus === 'COMPLETED') return 'completed';
  if (currentStatus === 'ESCALATED' || currentStatus === 'FAILED') {
    const ci = STATUS_FLOW.indexOf(currentStatus);
    const si = STATUS_FLOW.indexOf(stepKey);
    if (si < ci) return 'completed';
    if (si === ci) return 'failed';
    return '';
  }
  const ci = STATUS_FLOW.indexOf(currentStatus);
  const si = STATUS_FLOW.indexOf(stepKey);
  if (si < ci) return 'completed';
  if (si === ci) return 'active';
  return '';
}

const ACTION_ICONS = {
  STATE_TRANSITION: '🔄',
  TOOL_INVOCATION:  '🔧',
  LLM_REASONING:    '🧠',
  ESCALATION:       '🚨',
  SYSTEM_ERROR:     '💥',
};

function TimelineEntry({ log }) {
  const [open, setOpen] = useState(false);
  const ts = new Date(log.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const hasDetail = log.tool_inputs || log.tool_outputs || log.llm_reasoning;

  return (
    <div className="timeline-entry">
      <div className="timeline-dot">
        <span>{ACTION_ICONS[log.action_type] || '•'}</span>
      </div>
      <div className="timeline-body">
        <div className="timeline-header" onClick={() => hasDetail && setOpen(o => !o)} style={{ cursor: hasDetail ? 'pointer' : 'default' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flex: 1 }}>
            <Badge value={log.action_type} />
            <span style={{ fontSize: '0.875rem', fontWeight: 500 }}>{log.message || log.action_type}</span>
          </div>
          <span style={{ fontSize: '0.75rem', color: 'hsl(var(--text-muted))', flexShrink: 0 }}>{ts}</span>
          {hasDetail && <span style={{ fontSize: '0.75rem', color: 'hsl(var(--text-muted))' }}>{open ? '▲' : '▼'}</span>}
        </div>
        {open && hasDetail && (
          <div className="timeline-detail">
            {log.tool_inputs && (
              <div className="json-block">
                <span className="json-label">Inputs</span>
                <pre>{JSON.stringify(log.tool_inputs, null, 2)}</pre>
              </div>
            )}
            {log.tool_outputs && (
              <div className="json-block">
                <span className="json-label">Outputs</span>
                <pre>{JSON.stringify(log.tool_outputs, null, 2)}</pre>
              </div>
            )}
            {log.llm_reasoning && (
              <div className="json-block">
                <span className="json-label">LLM Reasoning</span>
                <pre>{JSON.stringify(log.llm_reasoning, null, 2)}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function LeadDetail({ leadId, onClose }) {
  const [lead, setLead]       = useState(null);
  const [status, setStatus]   = useState(null);
  const [logs, setLogs]       = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState('');
  const intervalRef           = useRef(null);

  const refresh = async () => {
    try {
      const [leadData, statusData, auditData] = await Promise.all([
        api.getLead(leadId),
        api.getLeadStatus(leadId),
        api.getLeadAuditLogs(leadId).catch(() => []),
      ]);
      setLead(leadData);
      setStatus(statusData);
      setLogs(auditData);
      setError('');

      if (TERMINAL.has(statusData.status)) {
        clearInterval(intervalRef.current);
      }
    } catch (err) {
      setError(err.message || 'Failed to load lead details.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    intervalRef.current = setInterval(refresh, 2500);
    return () => clearInterval(intervalRef.current);
  }, [leadId]);

  const isActive = status && !TERMINAL.has(status.status);

  return (
    <div className="modal-overlay" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal-content glass-panel detail-modal">
        {/* Modal Header */}
        <div className="detail-header">
          <div>
            <h3 style={{ fontSize: '1.25rem', fontFamily: 'var(--font-heading)', fontWeight: 700 }}>
              Agent Processing Monitor
            </h3>
            <p style={{ fontSize: '0.775rem', color: 'hsl(var(--text-muted))', fontFamily: 'monospace', marginTop: 2 }}>
              ID: {leadId}
            </p>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
            {isActive && (
              <span className="live-badge">
                <span className="live-dot pulse" />
                LIVE
              </span>
            )}
            <button onClick={onClose} className="close-btn">✕</button>
          </div>
        </div>

        {error && <div className="alert alert-error" style={{ margin: '1rem 1.5rem 0' }}>{error}</div>}

        {loading ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem' }}><Spinner /></div>
        ) : (
          <div className="detail-body">
            {/* Status Tracker */}
            <div className="status-tracker">
              {STATUS_FLOW.map((key, i) => {
                const s = stepState(key, status?.status);
                return (
                  <React.Fragment key={key}>
                    <div className={`status-step ${s}`}>
                      <div className={`status-node ${s === 'active' ? 'pulse' : ''}`}>
                        {s === 'completed' ? '✓' : s === 'failed' ? '✕' : i + 1}
                      </div>
                      <span className="status-label">{key}</span>
                    </div>
                    {i < STATUS_FLOW.length - 1 && <div className={`step-connector ${s === 'completed' ? 'done' : ''}`} />}
                  </React.Fragment>
                );
              })}
            </div>

            {/* Resolution banner */}
            {status?.status === 'COMPLETED' && (
              <div className="alert alert-success">🎉 Lead successfully processed and routed by the autonomous agent.</div>
            )}
            {status?.status === 'ESCALATED' && (
              <div className="alert alert-warning">⚠️ Low confidence decision — escalated to a senior sales representative.</div>
            )}
            {status?.status === 'FAILED' && (
              <div className="alert alert-error">💥 Agent faulted after {status.retry_count} retries: {status.last_error || 'unknown error'}</div>
            )}

            {/* Lead meta + assessment */}
            {lead && (
              <div className="detail-meta">
                <div className="meta-card">
                  <p className="meta-label">Contact</p>
                  <p className="meta-value">{[lead.first_name, lead.last_name].filter(Boolean).join(' ') || '—'}</p>
                  <p className="meta-sub">{lead.email}</p>
                  {lead.company  && <p className="meta-sub">{lead.job_title ? `${lead.job_title} · ` : ''}{lead.company}</p>}
                </div>
                <div className="meta-card">
                  <p className="meta-label">AI Assessment</p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.25rem' }}>
                    <Badge value={lead.priority} />
                    <span style={{ fontSize: '0.8rem', color: 'hsl(var(--text-secondary))' }}>priority</span>
                  </div>
                  <p className="meta-sub" style={{ marginTop: '0.5rem' }}>Budget: <strong>${(lead.budget || 0).toLocaleString()}</strong></p>
                  <div style={{ marginTop: '0.25rem' }}>
                    <Badge value={status?.status || 'RECEIVED'} />
                  </div>
                </div>
              </div>
            )}

            {/* Agent Audit Timeline */}
            <div className="timeline-section">
              <h4 className="section-title">
                <span>🔍 Agent Execution Timeline</span>
                <span style={{ fontWeight: 400, fontSize: '0.8rem', color: 'hsl(var(--text-muted))' }}>
                  {logs.length} steps recorded
                </span>
              </h4>
              {logs.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '2rem', color: 'hsl(var(--text-muted))', fontSize: '0.875rem' }}>
                  {isActive ? 'Agent is initialising — logs will appear shortly…' : 'No audit logs recorded.'}
                </div>
              ) : (
                <div className="timeline">
                  {logs.map(log => <TimelineEntry key={log.id} log={log} />)}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
