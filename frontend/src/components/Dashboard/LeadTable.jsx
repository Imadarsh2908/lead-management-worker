import React, { useEffect, useState, useCallback } from 'react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import Badge from '../Common/Badge';
import Spinner from '../Common/Spinner';
import Card from '../Common/Card';
import ConfirmDialog from '../Common/ConfirmDialog';

function Avatar({ name }) {
  const initials = (name || '?').split(' ').map(n => n[0]).join('').slice(0, 2).toUpperCase();
  const hue = [...(name || '')].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
      width: 34, height: 34, borderRadius: '50%', fontSize: '0.8rem', fontWeight: 700,
      background: `hsl(${hue} 60% 28%)`, color: `hsl(${hue} 80% 80%)`,
      border: `2px solid hsl(${hue} 50% 35%)`, flexShrink: 0,
    }}>
      {initials}
    </span>
  );
}

const PAGE_SIZE = 10;

export default function LeadTable({ onViewDetails }) {
  const { canDeleteLeads } = useAuth();
  const [leads, setLeads] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getLeads(page, PAGE_SIZE);
      setLeads(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err.message || 'Failed to load leads.');
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => { fetchLeads(); }, [fetchLeads]);

  const requestDelete = (e, id) => {
    e.stopPropagation();
    setConfirmDeleteId(id);
  };

  const confirmDelete = async () => {
    const id = confirmDeleteId;
    setConfirmDeleteId(null);
    try {
      await api.deleteLead(id);
      fetchLeads();
    } catch (err) {
      alert(err.message);
    }
  };

  const fmt = (d) => new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const fullName = (l) => [l.first_name, l.last_name].filter(Boolean).join(' ') || l.email;

  return (
    <Card style={{ padding: '0' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1.5rem 1.75rem', borderBottom: '1px solid var(--border-glass)' }}>
        <div>
          <h3 style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: '1.1rem' }}>Active Pipeline</h3>
          <p style={{ fontSize: '0.825rem', color: 'hsl(var(--text-secondary))', marginTop: 2 }}>{total} leads total · click a row to inspect</p>
        </div>
        <button className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.45rem 1rem' }} onClick={fetchLeads} disabled={loading}>
          {loading ? <Spinner size={14} /> : '↻ Refresh'}
        </button>
      </div>

      {error && <div className="alert alert-error" style={{ margin: '1rem 1.75rem' }}>{error}</div>}

      {/* Table */}
      {loading && leads.length === 0 ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem 0' }}><Spinner size={32} /></div>
      ) : leads.length === 0 ? (
        <div className="empty-state">
          <span style={{ fontSize: '2.5rem', display: 'block', marginBottom: '1rem' }}>📭</span>
          <p>No leads yet — ingest one to get started.</p>
        </div>
      ) : (
        <>
          <div className="table-container" style={{ borderRadius: 0, border: 'none' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Contact</th>
                  <th>Company · Title</th>
                  <th>Priority</th>
                  <th>Ingested</th>
                  {canDeleteLeads && <th style={{ textAlign: 'right' }}>Action</th>}
                </tr>
              </thead>
              <tbody>
                {leads.map(lead => (
                  <tr key={lead.id} onClick={() => onViewDetails(lead.id)} style={{ cursor: 'pointer' }}>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                        <Avatar name={fullName(lead)} />
                        <div>
                          <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>{fullName(lead)}</div>
                          <div style={{ fontSize: '0.78rem', color: 'hsl(var(--text-secondary))' }}>{lead.email}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <div style={{ fontWeight: 500, fontSize: '0.88rem' }}>{lead.company || '—'}</div>
                      <div style={{ fontSize: '0.78rem', color: 'hsl(var(--text-secondary))' }}>{lead.job_title || '—'}</div>
                    </td>
                    <td><Badge value={lead.priority} /></td>
                    <td style={{ fontSize: '0.82rem', color: 'hsl(var(--text-secondary))' }}>{fmt(lead.created_at)}</td>
                    {canDeleteLeads && (
                      <td style={{ textAlign: 'right' }}>
                        <button className="btn btn-danger" style={{ padding: '0.3rem 0.7rem', fontSize: '0.78rem' }}
                          onClick={e => requestDelete(e, lead.id)}>Delete</button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1rem 1.75rem', borderTop: '1px solid var(--border-glass)' }}>
            <span style={{ fontSize: '0.82rem', color: 'hsl(var(--text-secondary))' }}>
              Page {page} of {totalPages}
            </span>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button className="btn btn-secondary" style={{ padding: '0.4rem 0.9rem', fontSize: '0.82rem' }}
                disabled={page <= 1 || loading} onClick={() => setPage(p => p - 1)}>← Prev</button>
              <button className="btn btn-secondary" style={{ padding: '0.4rem 0.9rem', fontSize: '0.82rem' }}
                disabled={page >= totalPages || loading} onClick={() => setPage(p => p + 1)}>Next →</button>
            </div>
          </div>
        </>
      )}
      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete this lead?"
        message="This soft-deletes the lead — it's removed from the pipeline but kept in the database for audit purposes."
        confirmLabel="Delete"
        danger
        onConfirm={confirmDelete}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </Card>
  );
}
