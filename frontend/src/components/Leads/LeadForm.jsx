import React, { useState } from 'react';
import { api } from '../../services/api';
import Spinner from '../Common/Spinner';
import Card from '../Common/Card';

export default function LeadForm({ onLeadSubmitted }) {
  const [form, setForm] = useState({ email: '', first_name: '', last_name: '', phone: '', company: '', job_title: '', budget: '' });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(null);

  const set = (k) => (e) => setForm(f => ({ ...f, [k]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.email) { setError('Email is required.'); return; }
    setLoading(true); setError(''); setSuccess(null);

    const payload = {
      email: form.email,
      first_name: form.first_name || null,
      last_name:  form.last_name  || null,
      phone:      form.phone      || null,
      company:    form.company    || null,
      job_title:  form.job_title  || null,
      budget:     form.budget ? parseFloat(form.budget) : 0.0,
    };

    try {
      const resp = await api.createLead(payload);
      setSuccess(resp);
      setForm({ email: '', first_name: '', last_name: '', phone: '', company: '', job_title: '', budget: '' });
      if (onLeadSubmitted) {
        setTimeout(() => onLeadSubmitted(resp.id), 1000);
      }
    } catch (err) {
      setError(err.message || 'Submission failed.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="lead-form-wrapper">
      <div className="lead-form-header">
        <h2>Ingest New Lead</h2>
        <p>The AI agent will autonomously validate, enrich, score, and route this lead in the background.</p>
      </div>

      <Card>
        {error   && <div className="alert alert-error"   style={{ marginBottom: '1.5rem' }}>{error}</div>}
        {success && (
          <div className="alert alert-success" style={{ marginBottom: '1.5rem' }}>
            <strong>Lead accepted!</strong> Agent workflow launched for <code style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>{success.id}</code>. Redirecting to live tracker…
          </div>
        )}

        <form onSubmit={handleSubmit}>
          {/* Email — full width */}
          <div className="form-group">
            <label className="form-label">Email Address <span style={{ color: '#f87171' }}>*</span></label>
            <input className="form-input" type="email" value={form.email} onChange={set('email')} placeholder="contact@company.com" required />
          </div>

          {/* Name row */}
          <div className="grid-2">
            <div className="form-group">
              <label className="form-label">First Name</label>
              <input className="form-input" type="text" value={form.first_name} onChange={set('first_name')} placeholder="Jane" />
            </div>
            <div className="form-group">
              <label className="form-label">Last Name</label>
              <input className="form-input" type="text" value={form.last_name} onChange={set('last_name')} placeholder="Doe" />
            </div>
          </div>

          {/* Company row */}
          <div className="grid-2">
            <div className="form-group">
              <label className="form-label">Company</label>
              <input className="form-input" type="text" value={form.company} onChange={set('company')} placeholder="Acme Corp" />
            </div>
            <div className="form-group">
              <label className="form-label">Job Title</label>
              <input className="form-input" type="text" value={form.job_title} onChange={set('job_title')} placeholder="CTO" />
            </div>
          </div>

          {/* Phone + Budget */}
          <div className="grid-2" style={{ marginBottom: '2rem' }}>
            <div className="form-group">
              <label className="form-label">Phone</label>
              <input className="form-input" type="text" value={form.phone} onChange={set('phone')} placeholder="+1 555-0199" />
            </div>
            <div className="form-group">
              <label className="form-label">Budget (USD)</label>
              <input className="form-input" type="number" value={form.budget} onChange={set('budget')} placeholder="150000" min="0" />
            </div>
          </div>

          <button type="submit" className="btn btn-primary btn-full" disabled={loading}>
            {loading ? <><Spinner size={18} />&nbsp;Submitting…</> : 'Launch Agent Pipeline →'}
          </button>
        </form>
      </Card>

      {/* How it works */}
      <div className="pipeline-steps">
        {['Validate', 'Enrich', 'Score', 'Route', 'Notify'].map((s, i) => (
          <React.Fragment key={s}>
            <div className="pipeline-step"><span className="pipeline-num">{i + 1}</span><span>{s}</span></div>
            {i < 4 && <span className="pipeline-arrow">→</span>}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
