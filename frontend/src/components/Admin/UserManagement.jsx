import React, { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';
import { useAuth } from '../../context/AuthContext';
import Badge from '../Common/Badge';
import Spinner from '../Common/Spinner';
import Card from '../Common/Card';
import ConfirmDialog from '../Common/ConfirmDialog';

export default function UserManagement() {
  const { user: currentUser } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [actingId, setActingId] = useState(null);
  const [confirmRevoke, setConfirmRevoke] = useState(null); // user pending revoke confirmation, or null

  // Create-user form state
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('Sales');
  const [createError, setCreateError] = useState('');
  const [creating, setCreating] = useState(false);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setUsers(await api.getUsers());
    } catch (err) {
      setError(err.message || 'Failed to load users.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchUsers(); }, [fetchUsers]);

  const handleCreate = async (e) => {
    e.preventDefault();
    setCreateError('');
    if (newUsername.trim().length < 3) { setCreateError('Username must be at least 3 characters.'); return; }
    if (newPassword.length < 8) { setCreateError('Password must be at least 8 characters.'); return; }
    setCreating(true);
    try {
      await api.createUser(newUsername.trim(), newPassword, newRole);
      setNewUsername('');
      setNewPassword('');
      setNewRole('Sales');
      fetchUsers();
    } catch (err) {
      setCreateError(err.message || 'Failed to create user.');
    } finally {
      setCreating(false);
    }
  };

  const requestRevoke = (u) => setConfirmRevoke(u);

  const confirmRevokeAccess = async () => {
    const u = confirmRevoke;
    setConfirmRevoke(null);
    setActingId(u.id);
    try {
      await api.revokeUserAccess(u.id);
      fetchUsers();
    } catch (err) {
      alert(err.message);
    } finally {
      setActingId(null);
    }
  };

  const handleRestore = async (u) => {
    setActingId(u.id);
    try {
      await api.restoreUserAccess(u.id);
      fetchUsers();
    } catch (err) {
      alert(err.message);
    } finally {
      setActingId(null);
    }
  };

  const fmt = (d) => d
    ? new Date(d).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
    : 'Never';

  return (
    <div className="lead-form-wrapper">
      <div className="lead-form-header">
        <h2>User Access</h2>
        <p>See who has access to the app, and revoke it instantly if needed — a revoked user is logged out on their next request.</p>
      </div>

      <Card style={{ marginBottom: '1.5rem' }}>
        <h3 style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: '1.1rem', marginBottom: '1rem' }}>Grant Access</h3>
        {createError && <div className="alert alert-error" style={{ marginBottom: '1rem' }}>{createError}</div>}
        <form onSubmit={handleCreate} style={{ display: 'flex', gap: '0.75rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div className="form-group" style={{ margin: 0, flex: '1 1 160px' }}>
            <label className="form-label">Username</label>
            <input
              type="text"
              className="form-input"
              value={newUsername}
              onChange={e => setNewUsername(e.target.value)}
              placeholder="e.g. jordan_sales"
              autoComplete="off"
            />
          </div>
          <div className="form-group" style={{ margin: 0, flex: '1 1 160px' }}>
            <label className="form-label">Password</label>
            <input
              type="password"
              className="form-input"
              value={newPassword}
              onChange={e => setNewPassword(e.target.value)}
              placeholder="At least 8 characters"
              autoComplete="new-password"
            />
          </div>
          <div className="form-group" style={{ margin: 0, flex: '0 1 140px' }}>
            <label className="form-label">Role</label>
            <select className="form-input" value={newRole} onChange={e => setNewRole(e.target.value)}>
              <option value="Admin">Admin</option>
              <option value="Sales">Sales</option>
              <option value="Operator">Operator</option>
            </select>
          </div>
          <button type="submit" className="btn btn-primary" style={{ padding: '0.6rem 1.25rem', fontSize: '0.875rem' }} disabled={creating}>
            {creating ? <Spinner size={16} /> : '+ Add User'}
          </button>
        </form>
      </Card>

      <Card style={{ padding: '0' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '1.5rem 1.75rem', borderBottom: '1px solid var(--border-glass)' }}>
          <div>
            <h3 style={{ fontFamily: 'var(--font-heading)', fontWeight: 700, fontSize: '1.1rem' }}>Accounts</h3>
            <p style={{ fontSize: '0.825rem', color: 'hsl(var(--text-secondary))', marginTop: 2 }}>{users.length} account{users.length === 1 ? '' : 's'} total</p>
          </div>
          <button className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.45rem 1rem' }} onClick={fetchUsers} disabled={loading}>
            {loading ? <Spinner size={14} /> : '↻ Refresh'}
          </button>
        </div>

        {error && <div className="alert alert-error" style={{ margin: '1rem 1.75rem' }}>{error}</div>}

        {loading && users.length === 0 ? (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '4rem 0' }}><Spinner size={32} /></div>
        ) : (
          <div className="table-container" style={{ borderRadius: 0, border: 'none' }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Username</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th>Last Login</th>
                  <th style={{ textAlign: 'right' }}>Action</th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => {
                  const isSelf = u.username === currentUser?.username;
                  return (
                    <tr key={u.id}>
                      <td style={{ fontWeight: 600, fontSize: '0.9rem' }}>
                        {u.username}{isSelf && <span style={{ color: 'hsl(var(--text-secondary))', fontWeight: 400 }}> (you)</span>}
                      </td>
                      <td style={{ fontSize: '0.88rem' }}>{u.role}</td>
                      <td><Badge value={u.is_active ? 'ACTIVE' : 'REVOKED'} /></td>
                      <td style={{ fontSize: '0.82rem', color: 'hsl(var(--text-secondary))' }}>{fmt(u.last_login_at)}</td>
                      <td style={{ textAlign: 'right' }}>
                        {u.is_active ? (
                          <button
                            className="btn btn-danger"
                            style={{ padding: '0.3rem 0.7rem', fontSize: '0.78rem' }}
                            disabled={isSelf || actingId === u.id}
                            title={isSelf ? "You can't revoke your own access" : undefined}
                            onClick={() => requestRevoke(u)}
                          >
                            {actingId === u.id ? <Spinner size={12} /> : 'Revoke'}
                          </button>
                        ) : (
                          <button
                            className="btn btn-secondary"
                            style={{ padding: '0.3rem 0.7rem', fontSize: '0.78rem' }}
                            disabled={actingId === u.id}
                            onClick={() => handleRestore(u)}
                          >
                            {actingId === u.id ? <Spinner size={12} /> : 'Restore'}
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <ConfirmDialog
        open={confirmRevoke !== null}
        title="Revoke access?"
        message={`Revoke access for "${confirmRevoke?.username}"? They will be logged out immediately, even mid-session.`}
        confirmLabel="Revoke"
        danger
        onConfirm={confirmRevokeAccess}
        onCancel={() => setConfirmRevoke(null)}
      />
    </div>
  );
}
