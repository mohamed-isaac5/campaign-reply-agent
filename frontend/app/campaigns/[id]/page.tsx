'use client';
import Link from 'next/link';
import { use, useEffect, useState } from 'react';
import { ACTION_LABELS, INTENT_LABELS, api } from '../../lib/api';

export default function CampaignDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<any>(null);
  const [open, setOpen] = useState<number | null>(null);
  const [error, setError] = useState('');
  const load = () => api('/api/campaigns/' + id).then(d => { setData(d); setError(''); }).catch(() => setError('Could not load this campaign. Try again.')); 
  useEffect(() => { load(); }, [id]);
  if (error || data?.error) return <div className="empty card"><h1>{data?.error ? 'Campaign not found' : 'Campaign could not be loaded'}</h1><p>{error || 'This campaign does not exist in this local database.'}</p><Link className="btn" href="/campaigns">Back to Campaigns</Link>{error && <button className="btn" onClick={load}>Try again</button>}</div>;
  if (!data) return <div className="empty" role="status">Loading campaign...</div>;
  const { campaign, stats, threads } = data;
  const total = Math.max(1, stats.by_classification.reduce((a: number, r: any) => a + r.c, 0));

  return (
    <>
      <div className="page-title">{campaign.name}</div>
      <div className="page-sub">{campaign.goal}</div>
      <div className="notice"><b>Effective rule: {campaign.autonomy?.version === 2 && campaign.autonomy?.auto_price ? 'Simple price-only replies enabled' : 'Review only'}</b><div>Recorded price: {campaign.facts?.price || 'No fixed price'}. Confidence must be at least 85%. Discounts, unknown facts and mixed questions require review.</div><div>Campaign: {campaign.status}. Pausing blocks automatic and approved sending.</div><button className="btn" onClick={async () => { try { await api('/api/campaigns/' + id + '/state', { method: 'POST', body: JSON.stringify({ status: campaign.status === 'active' ? 'paused' : 'active' }) }); load(); } catch {} }}>{campaign.status === 'active' ? 'Pause campaign' : 'Resume campaign'}</button></div>
      {campaign.steps?.plan?.length > 0 && (
        <div className="card" style={{ marginBottom: 12 }}>
          <b style={{ fontSize: 14 }}>Campaign steps</b>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
            {campaign.steps.plan.map((s: any) => (
              <span key={s.order} className="chip">{s.order}. {s.name}</span>
            ))}
          </div>
          <div className="note" style={{ marginTop: 8 }}>This is the campaign plan as you described it. Which step each contact is on stays unknown unless it comes from an imported campaign file with step mapping; we never guess it from messages.</div>
        </div>
      )}
      <div className="chips" style={{ marginBottom: 16 }}>
        <span className="chip">Replies observed: <b>{stats.observed_replies}</b></span>
        <span className="chip hot">Interested: <b>{stats.interested}</b></span>
        <span className="chip warn">Awaiting your decision: <b>{stats.awaiting_decision}</b></span>
        <span className="chip">Do not contact: <b>{stats.do_not_contact}</b></span>
        <span className="chip">Auto-replies skipped: <b>{stats.auto_replies_skipped}</b></span>
      </div>
      <div className="card" style={{ marginBottom: 16 }}>
        <b style={{ fontSize: 13 }}>Classifications</b>
        {stats.by_classification.map((r: any) => (
          <div key={r.classification} className="bar-row">
            <span className="lbl">{INTENT_LABELS[r.classification] || r.classification}</span>
            <div className="bar-track"><div className="bar" style={{ width: (r.c / total * 100) + '%' }} /></div>
            <b>{r.c}</b>
          </div>
        ))}
        <div className="note">{stats.totals_note}</div>
      </div>
      {threads.map((t: any) => {
        const last = t.messages[t.messages.length - 1];
        const intent = last?.classification;
        return (
          <div key={t.id} className="thread">
            <button type="button" className="thread-head" aria-expanded={open === t.id} aria-controls={"thread-content-" + t.id} onClick={() => setOpen(open === t.id ? null : t.id)}>
              <b>{t.contact_name}</b>
              <span style={{ fontSize: 12, color: 'var(--ink2)' }}>{t.contact_email}</span>
              <span className="spacer" />
              {last?.created_at && <span style={{ fontSize: 11, color: 'var(--ink2)' }}>{new Date(last.created_at * 1000).toLocaleString('en', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}</span>}
              {t.do_not_contact ? <span className="tag red">Do not contact</span> : null}
              {intent && <span className={'tag' + (intent === 'unclear' || intent === 'auto_reply' ? ' gray' : '')}>{INTENT_LABELS[intent] || intent}</span>}
            </button>
            {open === t.id && (
              <div className="thread-body" id={"thread-content-" + t.id}>
                {t.messages.map((m: any) => (
                  <div key={m.id} className="msg">
                    <div className="who">{m.direction === 'in' ? t.contact_name : 'Us'}</div>
                    {m.body}
                    {m.class_reason && (
                      <div className="meta">
                        {INTENT_LABELS[m.classification] || m.classification}, confidence {Math.round((m.confidence || 0) * 100)}% — {m.class_reason}
                      </div>
                    )}
                  </div>
                ))}
                {t.actions.map((a: any) => (
                  <div key={a.id} className="action-row">
                    <span className="tag">{ACTION_LABELS[a.kind] || a.kind}</span>
                    <span className="action-reason">{a.reason}</span>
                    <span style={{ fontSize: 11, color: 'var(--ink2)' }}>{a.status === 'sent_simulated' ? 'Recorded (simulation)' : a.status === 'pending' ? 'Awaiting your decision' : a.status}</span>
                    {['pending', 'sending', 'uncertain'].includes(a.status) && <Link className="btn primary" href={'/review#action-' + a.id}>Open full review & editor</Link>}
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
      {threads.length === 0 && <div className="empty card">No replies observed on this campaign yet.</div>}
    </>
  );
}
