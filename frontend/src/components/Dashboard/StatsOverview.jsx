import React, { useEffect, useState } from 'react';
import { api } from '../../services/api';
import Spinner from '../Common/Spinner';
import Badge from '../Common/Badge';
import Card from '../Common/Card';

function StatCard({ label, value, icon, accent }) {
  return (
    <Card hoverable style={{ padding: '1.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <p style={{ color: 'hsl(var(--text-secondary))', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '0.5rem' }}>{label}</p>
          <p style={{ fontSize: '2.25rem', fontFamily: 'var(--font-heading)', fontWeight: 800, background: accent, WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>{value}</p>
        </div>
        <span style={{ fontSize: '1.75rem', opacity: 0.7 }}>{icon}</span>
      </div>
    </Card>
  );
}

export default function StatsOverview() {
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.getLeads(1, 100);
        const items = data.items || [];
        const high   = items.filter(l => l.priority === 'HIGH').length;
        const medium = items.filter(l => l.priority === 'MEDIUM').length;
        const low    = items.filter(l => l.priority === 'LOW').length;
        const unassigned = items.filter(l => l.priority === 'UNASSIGNED').length;
        setStats({ total: data.total, high, medium, low, unassigned });
      } catch {
        setStats({ total: 0, high: 0, medium: 0, low: 0, unassigned: 0 });
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '2rem' }}>
        <Spinner size={28} />
      </div>
    );
  }

  return (
    <div className="stats-grid">
      <StatCard label="Total Leads" value={stats.total} icon="🎯"
        accent="linear-gradient(135deg, #a78bfa, #60a5fa)" />
      <StatCard label="High Priority" value={stats.high} icon="🔥"
        accent="linear-gradient(135deg, #f87171, #fb923c)" />
      <StatCard label="Medium Priority" value={stats.medium} icon="⚡"
        accent="linear-gradient(135deg, #fbbf24, #a3e635)" />
      <StatCard label="Pending Scoring" value={stats.unassigned} icon="⏳"
        accent="linear-gradient(135deg, #94a3b8, #64748b)" />
    </div>
  );
}
