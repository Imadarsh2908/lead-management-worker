import React, { useState } from 'react';
import { api } from '../api';

export default function Login({ onLoginSuccess }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [selectedPreset, setSelectedPreset] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const presets = [
    { name: 'Admin', username: 'admin_user', desc: 'Full access + delete privileges' },
    { name: 'Sales', username: 'sales_user', desc: 'Submit and view leads list' },
    { name: 'Operator', username: 'operator_user', desc: 'Submit leads only' },
  ];

  const handleSelectPreset = (preset) => {
    setSelectedPreset(preset.username);
    setUsername(preset.username);
    setPassword('password123');
    setError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username || !password) {
      setError('Please fill in all fields.');
      return;
    }

    setLoading(true);
    setError('');

    try {
      const user = await api.login(username, password);
      onLoginSuccess(user);
    } catch (err) {
      setError(err.message || 'Login failed. Please check credentials.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '80vh' }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '480px' }}>
        <h2 style={{ marginBottom: '0.5rem', textAlign: 'center', fontSize: '1.75rem' }}>Welcome to Lead Flow AI</h2>
        <p style={{ color: 'hsl(var(--text-secondary))', textAlign: 'center', marginBottom: '2rem', fontSize: '0.9rem' }}>
          Autonomous Lead Management Portal
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

        <div style={{ marginBottom: '1.5rem' }}>
          <span className="form-label" style={{ display: 'block', marginBottom: '0.75rem' }}>Quick Connect Profile:</span>
          <div className="preset-grid">
            {presets.map((preset) => (
              <div
                key={preset.name}
                className={`preset-card ${selectedPreset === preset.username ? 'selected' : ''}`}
                onClick={() => handleSelectPreset(preset)}
              >
                <h4>{preset.name}</h4>
                <p style={{ fontSize: '0.65rem' }}>{preset.desc}</p>
              </div>
            ))}
          </div>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">Username</label>
            <input
              type="text"
              className="form-input"
              value={username}
              onChange={(e) => {
                setUsername(e.target.value);
                setSelectedPreset(null);
              }}
              placeholder="e.g. admin_user"
            />
          </div>

          <div className="form-group" style={{ marginBottom: '2rem' }}>
            <label className="form-label">Password</label>
            <input
              type="password"
              className="form-input"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setSelectedPreset(null);
              }}
              placeholder="••••••••"
            />
          </div>

          <button type="submit" className="btn btn-primary" style={{ width: '100%' }} disabled={loading}>
            {loading ? 'Authenticating...' : 'Sign In'}
          </button>
        </form>
      </div>
    </div>
  );
}
