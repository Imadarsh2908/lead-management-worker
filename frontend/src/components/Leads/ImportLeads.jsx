import React, { useState } from 'react';
import { api } from '../../services/api';
import Spinner from '../Common/Spinner';
import Card from '../Common/Card';

const TABS = [
  { key: 'file', label: 'Upload file' },
  { key: 'paste', label: 'Paste data' },
  { key: 'email', label: 'From email' },
];

function ResultSummary({ result }) {
  if (!result) return null;
  return (
    <div className="alert alert-success" style={{ marginTop: '1.25rem' }}>
      <strong>Import processed.</strong>{' '}
      {result.created} created, {result.skipped_duplicates} skipped (duplicates),{' '}
      {result.errors} error{result.errors === 1 ? '' : 's'} out of {result.total} row
      {result.total === 1 ? '' : 's'}.
      {result.error_details && result.error_details.length > 0 && (
        <div style={{ marginTop: '0.75rem', overflowX: 'auto' }}>
          <table className="mini-table">
            <thead>
              <tr><th>Row</th><th>Email</th><th>Reason</th></tr>
            </thead>
            <tbody>
              {result.error_details.map((e, i) => (
                <tr key={i}>
                  <td style={{ fontVariantNumeric: 'tabular-nums' }}>{e.row}</td>
                  <td>{e.email || '—'}</td>
                  <td>{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function ImportLeads() {
  const [tab, setTab] = useState('file');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  // file tab
  const [file, setFile] = useState(null);
  // paste tab
  const [format, setFormat] = useState('csv');
  const [pasteData, setPasteData] = useState('');

  const reset = () => { setError(''); setResult(null); };

  const submitFile = async () => {
    if (!file) { setError('Choose a .csv or .xlsx file first.'); return; }
    setLoading(true); reset();
    try {
      setResult(await api.importLeadsFile(file));
    } catch (err) {
      setError(err.message || 'Upload failed.');
    } finally {
      setLoading(false);
    }
  };

  const submitPaste = async () => {
    if (!pasteData.trim()) { setError('Paste some rows first.'); return; }
    setLoading(true); reset();
    try {
      setResult(await api.importLeadsPaste(format, pasteData));
    } catch (err) {
      setError(err.message || 'Import failed.');
    } finally {
      setLoading(false);
    }
  };

  const csvPlaceholder =
    'email,first_name,company,job_title,budget\n' +
    'jane@globex.com,Jane,Globex,VP of Sales,750000\n' +
    'sam@initech.com,Sam,Initech,Director,120000';
  const jsonPlaceholder =
    '[\n  {"email": "jane@globex.com", "company": "Globex", "budget": 750000},\n' +
    '  {"email": "sam@initech.com", "job_title": "Director"}\n]';

  return (
    <div className="lead-form-wrapper">
      <div className="lead-form-header">
        <h2>Import Leads</h2>
        <p>Bring in leads in bulk. Every imported lead runs through the same AI agent pipeline as a manually-entered one.</p>
      </div>

      <Card>
        {/* Tabs */}
        <div className="tab-row" role="tablist">
          {TABS.map(t => (
            <button
              key={t.key}
              role="tab"
              aria-selected={tab === t.key}
              className={`tab-btn ${tab === t.key ? 'active' : ''}`}
              onClick={() => { setTab(t.key); reset(); }}
            >
              {t.label}
            </button>
          ))}
        </div>

        {error && <div className="alert alert-error" style={{ marginTop: '1.25rem' }}>{error}</div>}

        {/* FILE */}
        {tab === 'file' && (
          <div style={{ marginTop: '1.25rem' }}>
            <div className="form-group">
              <label className="form-label">Spreadsheet file (.csv or .xlsx)</label>
              <input
                className="form-input"
                type="file"
                accept=".csv,.xlsx"
                onChange={(e) => { setFile(e.target.files?.[0] || null); reset(); }}
              />
              <p className="hint-text">
                First row must be the header. Columns are matched flexibly — e.g.
                “Email Address”, “First Name”, “Company”, “Job Title”, “Phone”, “Budget”.
                Only email is required.
              </p>
            </div>
            <button className="btn btn-primary btn-full" onClick={submitFile} disabled={loading}>
              {loading ? <><Spinner size={18} />&nbsp;Importing…</> : 'Import file →'}
            </button>
          </div>
        )}

        {/* PASTE */}
        {tab === 'paste' && (
          <div style={{ marginTop: '1.25rem' }}>
            <div className="form-group">
              <label className="form-label">Format</label>
              <select className="form-input" value={format} onChange={(e) => { setFormat(e.target.value); reset(); }}>
                <option value="csv">CSV (with header row)</option>
                <option value="json">JSON (array of objects)</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Rows</label>
              <textarea
                className="form-input"
                style={{ minHeight: '160px', fontFamily: 'monospace', fontSize: '0.82rem' }}
                value={pasteData}
                onChange={(e) => setPasteData(e.target.value)}
                placeholder={format === 'csv' ? csvPlaceholder : jsonPlaceholder}
              />
            </div>
            <button className="btn btn-primary btn-full" onClick={submitPaste} disabled={loading}>
              {loading ? <><Spinner size={18} />&nbsp;Importing…</> : 'Import pasted rows →'}
            </button>
          </div>
        )}

        {/* EMAIL */}
        {tab === 'email' && (
          <div style={{ marginTop: '1.25rem' }}>
            <p style={{ marginBottom: '0.75rem' }}>
              Leads can be created automatically from incoming emails — the sender
              becomes a new lead. This runs through a secure webhook the backend exposes:
            </p>
            <pre className="code-block">POST {`{API_BASE}`}/v1/leads/inbound-email?token=YOUR_TOKEN</pre>
            <p className="hint-text">
              One-time setup (done in your email provider, not here): point a
              <strong> SendGrid</strong> or <strong>Mailgun Inbound Parse</strong> route at the URL
              above, and set <code>INBOUND_EMAIL_TOKEN</code> on the backend to match the
              <code>?token=</code> value. Until that token is configured, the webhook stays
              disabled (returns 404), so there's no open endpoint. Every email that arrives
              at your parse address then shows up here as a lead.
            </p>
          </div>
        )}

        <ResultSummary result={result} />
      </Card>
    </div>
  );
}
