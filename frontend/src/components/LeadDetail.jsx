import React, { useEffect, useState } from 'react';
import { api } from '../api';

export default function LeadDetail({ leadId, onClose }) {
  const [lead, setLead] = useState(null);
  const [statusInfo, setStatusInfo] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchStatusAndDetails = async () => {
    try {
      const statusData = await api.getLeadStatus(leadId);
      setStatusInfo(statusData);

      // Also get the lead record details (which contain priority, company info etc.)
      const leadData = await api.getLead(leadId);
      setLead(leadData);
      setError('');
    } catch (err) {
      setError(err.message || 'Failed to fetch lead status.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatusAndDetails();
    
    // Set up polling every 2 seconds
    const interval = setInterval(() => {
      fetchStatusAndDetails();
    }, 2000);

    return () => clearInterval(interval);
  }, [leadId]);

  // Steps mapping
  const steps = [
    { key: 'RECEIVED', label: 'Received' },
    { key: 'VALIDATING', label: 'Validating' },
    { key: 'ENRICHING', label: 'Enriching' },
    { key: 'ANALYZING', label: 'Analyzing' },
    { key: 'EXECUTING', label: 'Executing' },
    { key: 'RESOLUTION', label: 'Resolution' },
  ];

  const getStepStatus = (stepKey, index) => {
    if (!statusInfo) return '';
    const current = statusInfo.status;

    // Hard errors / terminations
    if (current === 'FAILED') {
      if (stepKey === 'RESOLUTION') return 'failed';
      return 'completed';
    }

    if (current === 'ESCALATED') {
      if (stepKey === 'RESOLUTION') return 'failed'; // Mark resolution red
      return 'completed';
    }

    if (current === 'COMPLETED') {
      return 'completed';
    }

    // Processing steps index lookup
    const statusOrder = ['RECEIVED', 'VALIDATING', 'ENRICHING', 'ANALYZING', 'EXECUTING'];
    const currentIndex = statusOrder.indexOf(current);

    if (stepKey === 'RESOLUTION') {
      return '';
    }

    const stepIndex = statusOrder.indexOf(stepKey);
    if (stepIndex < currentIndex) return 'completed';
    if (stepIndex === currentIndex) return 'active';
    return '';
  };

  const getResolutionText = () => {
    if (!statusInfo) return 'Pending';
    const status = statusInfo.status;
    if (status === 'COMPLETED') return 'Completed Successfully';
    if (status === 'ESCALATED') return 'Escalated to Human';
    if (status === 'FAILED') return 'Execution Failed';
    return 'Processing...';
  };

  const isWorkflowActive = () => {
    if (!statusInfo) return false;
    const s = statusInfo.status;
    return s !== 'COMPLETED' && s !== 'ESCALATED' && s !== 'FAILED';
  };

  return (
    <div className="modal-overlay">
      <div className="modal-content glass-panel" style={{ position: 'relative' }}>
        <button 
          onClick={onClose}
          style={{
            position: 'absolute',
            top: '1.25rem',
            right: '1.25rem',
            background: 'none',
            border: 'none',
            color: 'hsl(var(--text-secondary))',
            fontSize: '1.5rem',
            cursor: 'pointer',
            padding: '0.25rem'
          }}
        >
          &times;
        </button>

        <h3 style={{ fontSize: '1.4rem', marginBottom: '0.25rem' }}>Lead Processing Details</h3>
        <p style={{ color: 'hsl(var(--text-secondary))', fontSize: '0.85rem', marginBottom: '2rem' }}>
          Lead ID: <span style={{ fontFamily: 'monospace', color: 'hsl(var(--primary-hover))' }}>{leadId}</span>
        </p>

        {error && (
          <div style={{
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            border: '1px solid rgba(239, 68, 68, 0.2)',
            color: '#ef4444',
            padding: '0.75rem 1rem',
            borderRadius: '8px',
            marginBottom: '1.5rem',
            fontSize: '0.875rem'
          }}>
            {error}
          </div>
        )}

        {loading && !lead ? (
          <div style={{ textAlign: 'center', padding: '2rem 0', color: 'hsl(var(--text-secondary))' }}>
            Fetching processing pipeline status...
          </div>
        ) : (
          <>
            {/* Status Step Tracker */}
            <div className="status-tracker">
              {steps.map((step, idx) => (
                <div key={step.key} className={`status-step ${getStepStatus(step.key, idx)}`}>
                  <div className="status-node">
                    {getStepStatus(step.key, idx) === 'completed' ? '✓' : idx + 1}
                  </div>
                  <span className="status-label">{step.label}</span>
                </div>
              ))}
            </div>

            {/* Current State Info */}
            <div style={{
              backgroundColor: 'rgba(255, 255, 255, 0.02)',
              border: '1px solid var(--border-glass)',
              borderRadius: '12px',
              padding: '1.25rem',
              marginBottom: '1.5rem',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center'
            }}>
              <div>
                <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'hsl(var(--text-secondary))' }}>
                  Current Status
                </div>
                <div style={{ fontSize: '1.1rem', fontWeight: 700, marginTop: '0.15rem', color: isWorkflowActive() ? 'hsl(var(--primary-hover))' : 'inherit' }}>
                  {statusInfo?.status}
                </div>
              </div>
              <div>
                <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'hsl(var(--text-secondary))', textAlign: 'right' }}>
                  Resolution status
                </div>
                <div style={{ fontSize: '1rem', fontWeight: 600, marginTop: '0.15rem', textAlign: 'right', color: statusInfo?.status === 'COMPLETED' ? '#10b981' : (statusInfo?.status === 'ESCALATED' || statusInfo?.status === 'FAILED') ? '#ef4444' : 'hsl(var(--text-secondary))' }}>
                  {getResolutionText()}
                </div>
              </div>
            </div>

            {/* Lead Meta Card */}
            {lead && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1.5rem' }}>
                <div style={{ border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '1rem' }}>
                  <div style={{ fontSize: '0.75rem', color: 'hsl(var(--text-secondary))', marginBottom: '0.25rem' }}>Lead Target</div>
                  <div style={{ fontWeight: 600 }}>{lead.first_name || '—'} {lead.last_name || '—'}</div>
                  <div style={{ fontSize: '0.8rem', color: 'hsl(var(--text-secondary))' }}>{lead.email}</div>
                  <div style={{ fontSize: '0.8rem', color: 'hsl(var(--text-secondary))', marginTop: '0.25rem' }}>{lead.job_title ? `${lead.job_title} at ` : ''}{lead.company || 'Unknown Company'}</div>
                </div>

                <div style={{ border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '1rem' }}>
                  <div style={{ fontSize: '0.75rem', color: 'hsl(var(--text-secondary))', marginBottom: '0.25rem' }}>Agent Assessment</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.25rem' }}>
                    <span style={{ fontSize: '0.85rem' }}>Score Priority:</span>
                    <span className={`badge ${lead.priority === 'HIGH' ? 'badge-high' : lead.priority === 'MEDIUM' ? 'badge-medium' : lead.priority === 'LOW' ? 'badge-low' : 'badge-unassigned'}`}>
                      {lead.priority}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.8rem', color: 'hsl(var(--text-secondary))', marginTop: '0.5rem' }}>
                    Budget: ${lead.budget?.toLocaleString() || '0'}
                  </div>
                </div>
              </div>
            )}

            {/* Failure/Escalation details */}
            {statusInfo?.status === 'ESCALATED' && (
              <div style={{
                backgroundColor: 'rgba(245, 158, 11, 0.1)',
                border: '1px solid rgba(245, 158, 11, 0.2)',
                color: '#f59e0b',
                padding: '1rem',
                borderRadius: '8px',
                fontSize: '0.875rem',
                marginBottom: '1.5rem'
              }}>
                <strong>Human Intervention Required:</strong> The decision engine routed this lead for escalation. The sales representative has been notified via Slack webhook.
              </div>
            )}

            {statusInfo?.status === 'FAILED' && (
              <div style={{
                backgroundColor: 'rgba(239, 68, 68, 0.1)',
                border: '1px solid rgba(239, 68, 68, 0.2)',
                color: '#ef4444',
                padding: '1rem',
                borderRadius: '8px',
                fontSize: '0.875rem',
                marginBottom: '1.5rem'
              }}>
                <strong>Agent Execution Faulted:</strong> {statusInfo.last_error || 'Maximum retries exceeded.'}
                <div style={{ fontSize: '0.75rem', marginTop: '0.25rem', color: 'hsl(var(--text-secondary))' }}>
                  Retried {statusInfo.retry_count} times before terminal failure.
                </div>
              </div>
            )}

            {/* Polling Indicator */}
            {isWorkflowActive() && (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.5rem', color: 'hsl(var(--text-secondary))', fontSize: '0.85rem' }}>
                <span className="pulse" style={{ display: 'inline-block', width: '8px', height: '8px', borderRadius: '9999px', backgroundColor: 'hsl(var(--primary))' }} />
                <span>Autonomous AI Agent is scoring and routing the lead. Polling state...</span>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
