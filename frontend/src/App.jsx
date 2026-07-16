import React, { useState } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import Login from './components/Auth/Login';
import StatsOverview from './components/Dashboard/StatsOverview';
import LeadTable from './components/Dashboard/LeadTable';
import LeadForm from './components/Leads/LeadForm';
import LeadDetail from './components/Leads/LeadDetail';
import ImportLeads from './components/Leads/ImportLeads';

/* ─── Sidebar nav items ──────────────────────────────── */
function NavItem({ icon, label, active, onClick, disabled }) {
  return (
    <button
      className={`nav-item ${active ? 'active' : ''} ${disabled ? 'disabled' : ''}`}
      onClick={!disabled ? onClick : undefined}
      title={disabled ? 'Requires Sales or Admin role' : label}
    >
      <span className="nav-icon">{icon}</span>
      <span className="nav-label">{label}</span>
    </button>
  );
}

/* ─── Top Header bar ─────────────────────────────────── */
function Header() {
  const { user, signOut } = useAuth();
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="brand">
          <span className="brand-icon">⚡</span>
          <span className="brand-text">Lead Flow AI</span>
        </div>
      </div>
      <div className="topbar-right">
        <div className="user-chip">
          <span className="user-avatar">{(user?.username || '?')[0].toUpperCase()}</span>
          <span className="user-name">{user?.username}</span>
          <span className="user-role-tag">{user?.role}</span>
        </div>
        <button className="btn btn-ghost-danger" onClick={signOut}>Sign Out</button>
      </div>
    </header>
  );
}

/* ─── Sidebar ─────────────────────────────────────────── */
function Sidebar({ view, setView }) {
  const { canViewLeads } = useAuth();
  return (
    <aside className="sidebar">
      <NavItem icon="📊" label="Dashboard"     active={view === 'dashboard'} onClick={() => setView('dashboard')} disabled={!canViewLeads} />
      <NavItem icon="➕" label="Ingest Lead"   active={view === 'ingest'}    onClick={() => setView('ingest')} />
      <NavItem icon="📥" label="Import Leads"  active={view === 'import'}    onClick={() => setView('import')} />
    </aside>
  );
}

/* ─── Main shell (logged-in) ─────────────────────────── */
function Shell() {
  const { canViewLeads } = useAuth();
  const [view, setView]           = useState(canViewLeads ? 'dashboard' : 'ingest');
  const [detailId, setDetailId]   = useState(null);

  const handleLeadSubmitted = (id) => {
    setDetailId(id);
  };

  return (
    <div className="app-shell">
      <Header />
      <div className="shell-body">
        <Sidebar view={view} setView={setView} />
        <main className="shell-main">
          {view === 'dashboard' && canViewLeads && (
            <div className="page-content">
              <div className="page-title-row">
                <div>
                  <h1 className="page-title">Pipeline Dashboard</h1>
                  <p className="page-sub">Real-time overview of all leads processed by the AI agent.</p>
                </div>
                <button className="btn btn-primary" style={{ padding: '0.6rem 1.25rem', fontSize: '0.875rem' }}
                  onClick={() => setView('ingest')}>
                  + New Lead
                </button>
              </div>
              <StatsOverview />
              <LeadTable onViewDetails={setDetailId} />
            </div>
          )}

          {view === 'ingest' && (
            <div className="page-content">
              <LeadForm onLeadSubmitted={handleLeadSubmitted} />
            </div>
          )}

          {view === 'import' && (
            <div className="page-content">
              <ImportLeads />
            </div>
          )}
        </main>
      </div>

      {detailId && (
        <LeadDetail leadId={detailId} onClose={() => setDetailId(null)} />
      )}
    </div>
  );
}

/* ─── Root ───────────────────────────────────────────── */
export default function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

function AppContent() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: '100vh' }}>
        <span style={{ color: 'hsl(var(--text-secondary))' }}>Initialising…</span>
      </div>
    );
  }

  return user ? <Shell /> : <Login />;
}
