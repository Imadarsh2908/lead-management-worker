import React, { useState } from 'react';
import { api } from '../api';

export default function LeadForm({ onLeadSubmitted }) {
  const [email, setEmail] = useState('');
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [phone, setPhone] = useState('');
  const [company, setCompany] = useState('');
  const [jobTitle, setJobTitle] = useState('');
  const [budget, setBudget] = useState('');
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email) {
      setError('Primary contact email is required.');
      return;
    }

    setLoading(true);
    setError('');
    setSuccess(null);

    const payload = {
      email,
      first_name: firstName || null,
      last_name: lastName || null,
      phone: phone || null,
      company: company || null,
      job_title: jobTitle || null,
      budget: budget ? parseFloat(budget) : 0.0,
    };

    try {
      const response = await api.createLead(payload);
      setSuccess(response);
      
      // Reset form
      setEmail('');
      setFirstName('');
      setLastName('');
      setPhone('');
      setCompany('');
      setJobTitle('');
      setBudget('');

      // Auto redirect to tracking modal after 1.5 seconds
      if (onLeadSubmitted) {
        setTimeout(() => {
          onLeadSubmitted(response.id);
        }, 1200);
      }
    } catch (err) {
      setError(err.message || 'Failed to submit lead.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="glass-panel" style={{ maxWidth: '720px', margin: '0 auto' }}>
      <h2 style={{ marginBottom: '0.5rem', fontSize: '1.5rem' }}>Ingest New Lead</h2>
      <p style={{ color: 'hsl(var(--text-secondary))', marginBottom: '2rem', fontSize: '0.9rem' }}>
        Submit a new lead into the pipeline. The autonomous agent will analyze, score, enrich, and route the lead in the background.
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

      {success && (
        <div style={{
          backgroundColor: 'rgba(16, 185, 129, 0.1)',
          border: '1px solid rgba(16, 185, 129, 0.2)',
          color: '#10b981',
          padding: '1rem',
          borderRadius: '8px',
          marginBottom: '1.5rem',
          fontSize: '0.9rem'
        }}>
          <strong>Lead Ingested Successfully!</strong> Launched agent workflow for ID: {success.id}. 
          Redirecting to live monitoring...
        </div>
      )}

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label className="form-label">Email Address *</label>
          <input
            type="email"
            className="form-input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="e.g. contact@business.com"
            required
          />
        </div>

        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">First Name</label>
            <input
              type="text"
              className="form-input"
              value={firstName}
              onChange={(e) => setFirstName(e.target.value)}
              placeholder="e.g. Jane"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Last Name</label>
            <input
              type="text"
              className="form-input"
              value={lastName}
              onChange={(e) => setLastName(e.target.value)}
              placeholder="e.g. Doe"
            />
          </div>
        </div>

        <div className="grid-2">
          <div className="form-group">
            <label className="form-label">Company Name</label>
            <input
              type="text"
              className="form-input"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="e.g. Acme Corp"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Job Title</label>
            <input
              type="text"
              className="form-input"
              value={jobTitle}
              onChange={(e) => setJobTitle(e.target.value)}
              placeholder="e.g. CTO"
            />
          </div>
        </div>

        <div className="grid-2" style={{ marginBottom: '2rem' }}>
          <div className="form-group">
            <label className="form-label">Phone Number</label>
            <input
              type="text"
              className="form-input"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="e.g. +1 555-0199"
            />
          </div>
          <div className="form-group">
            <label className="form-label">Estimated Budget (USD)</label>
            <input
              type="number"
              className="form-input"
              value={budget}
              onChange={(e) => setBudget(e.target.value)}
              placeholder="e.g. 50000"
              min="0"
            />
          </div>
        </div>

        <button type="submit" className="btn btn-primary" style={{ width: '100%' }} disabled={loading}>
          {loading ? 'Submitting...' : 'Ingest and Launch Agent'}
        </button>
      </form>
    </div>
  );
}
