import React, { useState } from 'react';
import { useAuth } from '../../context/AuthContext';
import Spinner from '../Common/Spinner';

const PRESETS = [
  {
    role: 'Admin',
    username: 'admin_user',
    icon: '👑',
    color: '#a78bfa',
    description: 'Full access · delete · dashboard',
  },
  {
    role: 'Sales',
    username: 'sales_user',
    icon: '📊',
    color: '#60a5fa',
    description: 'View leads · submit · track',
  },
  {
    role: 'Operator',
    username: 'operator_user',
    icon: '⚙️',
    color: '#34d399',
    description: 'Submit leads only',
  },
];

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const pickPreset = (preset) => {
    setSelected(preset.username);
    setUsername(preset.username);
    setPassword('password123');
    setError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username || !password) { setError('Please enter credentials.'); return; }
    setLoading(true);
    setError('');
    try {
      await login(username, password);
    } catch (err) {
      setError(err.message || 'Login failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      {/* Left decorative panel */}
      <div className="login-hero">
        <div className="login-hero-content">
          <div className="login-logo">
            <span className="logo-icon">⚡</span>
            <span className="logo-text">Lead Flow AI</span>
          </div>
          <h1 className="login-tagline">Autonomous Lead<br />Management at Scale</h1>
          <p className="login-sub">AI-native pipeline that ingests, enriches, scores, and routes every lead — without human bottlenecks.</p>
          <div className="login-pills">
            {['LangGraph Agent', 'JWT Auth', 'Real-time Tracking'].map(p => (
              <span key={p} className="pill">{p}</span>
            ))}
          </div>
        </div>
        <div className="hero-glow" />
      </div>

      {/* Right auth panel */}
      <div className="login-form-side">
        <div className="login-card glass-panel">
          <h2 className="login-title">Welcome back</h2>
          <p className="login-hint">Select a role to quick-connect, or enter credentials manually.</p>

          {/* Preset role cards */}
          <div className="preset-grid" style={{ gridTemplateColumns: 'repeat(3, 1fr)' }}>
            {PRESETS.map((p) => (
              <button
                key={p.role}
                type="button"
                onClick={() => pickPreset(p)}
                className={`preset-card ${selected === p.username ? 'selected' : ''}`}
                style={{ '--preset-color': p.color }}
              >
                <span className="preset-icon">{p.icon}</span>
                <span className="preset-role">{p.role}</span>
                <span className="preset-desc">{p.description}</span>
              </button>
            ))}
          </div>

          {/* Divider */}
          <div className="login-divider"><span>or sign in manually</span></div>

          {error && <div className="alert alert-error">{error}</div>}

          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label className="form-label">Username</label>
              <input
                type="text"
                className="form-input"
                value={username}
                onChange={e => { setUsername(e.target.value); setSelected(null); }}
                placeholder="e.g. admin_user"
                autoComplete="username"
              />
            </div>
            <div className="form-group" style={{ marginBottom: '1.75rem' }}>
              <label className="form-label">Password</label>
              <input
                type="password"
                className="form-input"
                value={password}
                onChange={e => { setPassword(e.target.value); setSelected(null); }}
                placeholder="••••••••"
                autoComplete="current-password"
              />
            </div>

            <button type="submit" className="btn btn-primary btn-full" disabled={loading}>
              {loading ? <><Spinner size={18} />&nbsp;Authenticating…</> : 'Sign In →'}
            </button>
          </form>

          <p className="login-footer-note">All demo passwords are <code>password123</code></p>
        </div>
      </div>
    </div>
  );
}
