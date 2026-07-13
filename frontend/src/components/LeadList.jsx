import React, { useEffect, useState } from 'react';
import { api } from '../api';

export default function LeadList({ userRole, onViewDetails }) {
  const [leads, setLeads] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pageSize = 10;

  const fetchLeads = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await api.getLeads(page, pageSize);
      setLeads(data.items);
      setTotal(data.total);
    } catch (err) {
      setError(err.message || 'Failed to load leads.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLeads();
  }, [page]);

  const handleDelete = async (e, leadId) => {
    e.stopPropagation(); // Prevent opening details modal
    if (!window.confirm('Are you sure you want to delete this lead? (Soft delete)')) {
      return;
    }

    try {
      await api.deleteLead(leadId);
      // Refresh list
      fetchLeads();
    } catch (err) {
      alert(err.message || 'Failed to delete lead.');
    }
  };

  const getPriorityBadgeClass = (priority) => {
    const p = String(priority).toUpperCase();
    if (p === 'HIGH') return 'badge badge-high';
    if (p === 'MEDIUM') return 'badge badge-medium';
    if (p === 'LOW') return 'badge badge-low';
    return 'badge badge-unassigned';
  };

  const formatDate = (dateString) => {
    try {
      return new Date(dateString).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
    } catch (e) {
      return dateString;
    }
  };

  const totalPages = Math.ceil(total / pageSize) || 1;

  return (
    <div className="glass-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '2rem' }}>
        <div>
          <h2 style={{ fontSize: '1.5rem' }}>Active Leads pipeline</h2>
          <p style={{ color: 'hsl(var(--text-secondary))', fontSize: '0.9rem' }}>
            List of all incoming leads tracked by the autonomous scoring agent.
          </p>
        </div>
        <button className="btn btn-secondary" onClick={fetchLeads} disabled={loading}>
          Refresh
        </button>
      </div>

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

      {loading ? (
        <div style={{ textAlign: 'center', padding: '3rem 0', color: 'hsl(var(--text-secondary))' }}>
          Loading active leads...
        </div>
      ) : leads.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '4rem 0', color: 'hsl(var(--text-muted))', border: '1px dashed var(--border-glass)', borderRadius: '12px' }}>
          <p style={{ fontSize: '1.1rem', marginBottom: '0.5rem' }}>No leads found</p>
          <p style={{ fontSize: '0.875rem' }}>Submit a new lead to start the processing pipeline.</p>
        </div>
      ) : (
        <>
          <div className="table-container" style={{ marginBottom: '1.5rem' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Contact Info</th>
                  <th>Company & Title</th>
                  <th>Priority</th>
                  <th>Created At</th>
                  {userRole === 'Admin' && <th style={{ textAlign: 'right' }}>Actions</th>}
                </tr>
              </thead>
              <tbody>
                {leads.map((lead) => (
                  <tr key={lead.id} onClick={() => onViewDetails(lead.id)}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{lead.first_name} {lead.last_name}</div>
                      <div style={{ fontSize: '0.8rem', color: 'hsl(var(--text-secondary))' }}>{lead.email}</div>
                    </td>
                    <td>
                      <div>{lead.company || '—'}</div>
                      <div style={{ fontSize: '0.8rem', color: 'hsl(var(--text-secondary))' }}>{lead.job_title || '—'}</div>
                    </td>
                    <td>
                      <span className={getPriorityBadgeClass(lead.priority)}>{lead.priority}</span>
                    </td>
                    <td style={{ fontSize: '0.875rem', color: 'hsl(var(--text-secondary))' }}>
                      {formatDate(lead.created_at)}
                    </td>
                    {userRole === 'Admin' && (
                      <td style={{ textAlign: 'right' }}>
                        <button
                          className="btn btn-danger"
                          style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                          onClick={(e) => handleDelete(e, lead.id)}
                        >
                          Delete
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.875rem', color: 'hsl(var(--text-secondary))' }}>
              Page {page} of {totalPages} ({total} total leads)
            </span>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button
                className="btn btn-secondary"
                style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
                disabled={page <= 1 || loading}
                onClick={() => setPage(page - 1)}
              >
                Previous
              </button>
              <button
                className="btn btn-secondary"
                style={{ padding: '0.5rem 1rem', fontSize: '0.85rem' }}
                disabled={page >= totalPages || loading}
                onClick={() => setPage(page + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
