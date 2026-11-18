'use client';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

function fmt(ts?: number | null) {
  if (!ts) return 'No activity yet';
  return new Date(ts * 1000).toLocaleDateString('en', { day: 'numeric', month: 'short' });
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'active', label: 'Active' },
  { key: 'paused', label: 'Paused' },
];

export default function CampaignsPage() {
  const [camps, setCamps] = useState<any[] | null>(null);
  const [sim, setSim] = useState<any>(null);
  const [filter, setFilter] = useState('all');
  const [samples, setSamples] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uniqueDnc, setUniqueDnc] = useState<number | null>(null);
  useEffect(() => { setUniqueDnc(null); api('/api/campaign-overview-stats?include_samples=' + samples).then(s => setUniqueDnc(s.do_not_contact)).catch(() => {}); }, [samples, camps]);
  const load = () => { api('/api/campaigns').then(setCamps).catch(() => {}); api('/api/sim/peek').then(setSim).catch(() => {}); };
  useEffect(() => { load(); }, []);

  const all = (camps || []).filter(c => samples || !c.sample);
  const shown = all.filter((c) => filter === 'all' || (filter === 'active' ? c.status === 'active' : c.status !== 'active'));
  const totals = all.reduce((a, c) => ({
    replies: a.replies + c.observed_replies, awaiting: a.awaiting + c.awaiting_decision,
    auto: a.auto + (c.auto_replies_skipped || 0),
  }), { replies: 0, awaiting: 0, auto: 0 });

  return (
    <>
      <div className="eyebrow">CAMPAIGN OVERVIEW</div><h1 className="page-title">Campaigns</h1>
      <div className="page-sub">Only observed human replies are counted. Offline simulator events are not real campaign results.</div>

      <label className="check-line"><input type="checkbox" checked={samples} onChange={e => setSamples(e.target.checked)} /><span>Include fictional sample campaigns in this view and its totals</span></label>
      <div className="stat-strip">
        <div className="stat"><div className="stat-num">{all.length}</div><div className="stat-lbl">Campaigns</div></div>
        <div className="stat"><div className="stat-num">{totals.replies}</div><div className="stat-lbl">Replies observed</div></div>
        <div className="stat"><div className="stat-num warn-num">{totals.awaiting}</div><div className="stat-lbl">Awaiting your decision</div></div>
        <div className="stat"><div className="stat-num">{uniqueDnc ?? '...'}</div><div className="stat-lbl">Do-not-contact list</div></div>
        <div className="stat"><div className="stat-num">{totals.auto}</div><div className="stat-lbl">Auto-replies skipped</div></div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        {FILTERS.map((f) => (
          <button key={f.key} className={'filter-chip' + (filter === f.key ? ' active' : '')} onClick={() => setFilter(f.key)}>{f.label}</button>
        ))}
        <span className="spacer" />
        {sim && sim.remaining > 0 && (
          <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); try { await api('/api/sim/tick', { method: 'POST', body: '{}' }); load(); } catch {} finally { setBusy(false); } }}>
            Simulate next inbound reply ({sim.remaining})
          </button>
        )}
      </div>

      <div className="grid">
        {shown.map((c) => (
          <Link key={c.id} href={'/campaigns/' + c.id} className="card camp-card">
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <b>{c.name}</b>{c.sample && <span className="tag gray">Fictional sample</span>}
              <span className="spacer" />
              <span className={'status-pill ' + (c.status === 'active' ? 'on' : 'off')}>{c.status === 'active' ? 'Active' : 'Paused'}</span>
            </div>
            <div className="chips">
              <span className="chip">Replies: <b>{c.observed_replies}</b></span>
              <span className="chip hot">Interested: <b>{c.interested}</b></span>
              {c.awaiting_decision > 0 && <span className="chip warn">Awaiting your decision: <b>{c.awaiting_decision}</b></span>}
              {(c.do_not_contact || 0) > 0 && <span className="chip">Do not contact: <b>{c.do_not_contact}</b></span>}
            </div>
            <div className="camp-foot">Last activity: {fmt(c.last_activity)}</div>
          </Link>
        ))}
      </div>
      {shown.length === 0 && camps && all.length > 0 && <div className="empty card">No campaigns in this category.</div>}
      {all.length === 0 && camps && <div className="empty card">No campaigns yet. Start from Chat to introduce the tool to your work.</div>}
    </>
  );
}
