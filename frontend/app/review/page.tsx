'use client';
import { useCallback, useEffect, useState } from 'react';
import { INTENT_LABELS, api } from '../lib/api';

type Receipt = { status: string; detail: string; name: string };
export default function ReviewPage() {
  const [items, setItems] = useState<any[]>([]);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<Record<number, boolean>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [activity, setActivity] = useState<any[]>([]);
  const [followups, setFollowups] = useState<any[]>([]);
  const [loaded, setLoaded] = useState(false);
  const load = useCallback(async () => {
    try {
      const [queue, log, notes] = await Promise.all([api('/api/review'), api('/api/activity'), api('/api/followups')]);
      setItems(queue); setActivity(log); setFollowups(notes); setLoaded(true);
    } catch { /* api publishes the visible error banner */ }
  }, []);
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, [load]);
  async function decide(a: any, decision: string) {
    if (busy[a.id]) return;
    setBusy(b => ({ ...b, [a.id]: true }));
    setErrors(e => ({ ...e, [a.id]: '' }));
    try {
      const res = await api('/api/review/' + a.id, { method: 'POST', body: JSON.stringify({ decision, body: drafts[a.id] ?? a.body ?? '' }) });
      if (res.ok) {
        setReceipt({ name: a.contact_name, status: res.status, detail: res.detail || res.note });
        await load();
      } else { setErrors(e => ({ ...e, [a.id]: res.detail || 'Decision not completed' })); await load(); }
    } catch (e: any) { setErrors(v => ({ ...v, [a.id]: e.message || 'Could not reach the local server. Check before retrying.' })); }
    finally { setBusy(b => ({ ...b, [a.id]: false })); }
  }
  const labels: Record<string, string> = { approved_sent: 'Gmail accepted', approved_recorded: 'Recorded in simulation', sent: 'Gmail accepted', sent_simulated: 'Recorded in simulation', rejected: 'Rejected', cancelled: 'Cancelled after opt-out', stale: 'Superseded by newer context', uncertain: 'Outcome uncertain', sending: 'Send in progress', done: 'Recorded', followup_pending: 'Follow-up needs action' };
  return <>
    <div className="eyebrow">HUMAN REVIEW</div>
    <h1 className="page-title">Your decision, with the full context</h1>
    <p className="page-sub">Review the recipient, original question, reason and exact draft. Automatic replies and manual approvals both respect opt-outs, campaign pause and the kill switch.</p>
    {receipt && <div className="receipt" role="status"><b>{receipt.name}: {labels[receipt.status] || receipt.status}</b><div>{receipt.detail}</div><button className="btn" onClick={() => setReceipt(null)}>Dismiss</button></div>}
    <div className="section-heading"><h2>Reply queue</h2><span className="tag gray">{items.length} open</span><button className="btn" onClick={load}>Refresh</button></div>
    {items.map(a => {
      const last = (a.thread_messages || []).filter((m: any) => m.direction === 'in').pop();
      const editable = a.status === 'pending';
      return <section id={'action-' + a.id} key={a.id} className="card review-card">
        <header className="review-head"><div><b>{a.contact_name}</b><div className="contact-address">{a.contact_email}</div></div><span className="tag gray">{a.campaign_name}</span><span className="spacer" /><span className="tag">{INTENT_LABELS[last?.classification] || last?.classification || 'Review'}</span></header>
        <div className="decision-reason"><b>Why it needs you</b><p>{a.reason}</p></div>
        {a.context_stale && editable && <div className="notice error">A newer customer message arrived after this draft. Approval is disabled. Review the latest message and reject this superseded draft to clear it.</div>}
        {!editable && <div className="notice error"><b>{labels[a.status]}</b><p>No second send is allowed. Reconciliation checks the exact saved Message-ID, not a similar-looking reply.</p><button className="btn" disabled={busy[a.id]} onClick={async () => { setBusy(v => ({ ...v, [a.id]: true })); try { const r = await api('/api/review/' + a.id + '/reconcile', { method: 'POST', body: '{}' }); setErrors(v => ({ ...v, [a.id]: r.detail })); await load(); } catch {} finally { setBusy(v => ({ ...v, [a.id]: false })); } }}>Check original attempt in Gmail</button></div>}
        <details className="fact-details"><summary>Recorded business facts and effective rule</summary><dl><dt>Business</dt><dd>{a.recorded_facts?.business || 'Not recorded'}</dd><dt>Exact price</dt><dd>{a.recorded_facts?.price || 'No fixed price'}</dd><dt>Other supplied facts</dt><dd>{a.recorded_facts?.raw_facts || 'Not recorded'}</dd><dt>Automatic permission</dt><dd>{a.effective_rule?.version === 2 && a.effective_rule?.auto_price ? 'Simple price-only questions' : 'Off, human review required'}</dd></dl></details>
        <h3>Full conversation</h3>
        <div className="conversation-context">{(a.thread_messages || []).map((m: any, i: number) => <div key={i} className="msg"><div className="who">{m.direction === 'in' ? a.contact_name : 'Our message'} <time>{new Date(m.created_at * 1000).toLocaleString('en')}</time></div><div className="message-text" dir="auto">{m.body}</div>{m.class_reason && <div className="meta">{INTENT_LABELS[m.classification] || m.classification}, confidence {Math.round((m.confidence || 0) * 100)}%. {m.class_reason}</div>}</div>)}</div>
        <label className="field-label" htmlFor={'draft-' + a.id}>Exact reply to {a.contact_email}</label>
        <p className="meta">Reviewable draft, not an answer verified with the customer. Check every claim before approval. Clearing the editor leaves it empty.</p>
        <textarea id={'draft-' + a.id} className="draft-edit" disabled={!editable || busy[a.id]} value={drafts[a.id] ?? a.body ?? ''} onChange={e => setDrafts(d => ({ ...d, [a.id]: e.target.value }))} />
        {errors[a.id] && <div className="notice error" role="alert">{errors[a.id]}</div>}
        {editable && <div className="decision-buttons"><button className="btn primary" disabled={busy[a.id] || a.context_stale || !(drafts[a.id] ?? a.body ?? '').trim()} onClick={() => decide(a, 'approve')}>{busy[a.id] ? 'Saving decision...' : a.gmail_thread_id ? 'Approve exact reply' : 'Approve exact reply (simulation)'}</button><button className="btn danger" disabled={busy[a.id]} onClick={() => decide(a, 'reject')}>Reject, do not send</button></div>}
      </section>;
    })}
    {loaded && !items.length && <div className="empty card"><b>Queue is clear</b><p>No replies need your decision right now.</p></div>}
    <div className="section-heading"><h2>Follow-up requests</h2><span className="tag gray">Manual next steps</span></div>
    <p className="meta">These are requests to act later, not scheduled automations. Review the customer's wording and schedule in your source campaign tool.</p>
    {followups.map(a => <div className="card" key={a.id} style={{ marginBottom: 12 }}><b>{a.contact_name}</b><div className="contact-address">{a.contact_email}</div><blockquote dir="auto">{a.request}</blockquote><button className="btn" onClick={async () => { try { await api('/api/followups/' + a.id + '/handled', { method: 'POST', body: '{}' }); setReceipt({ name: a.contact_name, status: 'done', detail: 'Marked handled externally. Nothing was sent or scheduled here.' }); load(); } catch {} }}>I handled this in my campaign tool</button></div>)}
    {!followups.length && <p className="meta">No open follow-up requests.</p>}
    <div className="section-heading"><h2>Decision history</h2><span className="tag gray">Latest {activity.length} (up to 50)</span></div>
    <div className="card activity-list">{activity.map(a => <div className="activity-item" key={a.id}><div><b>{a.contact_name}</b><div className="meta">{a.reason}</div>{a.reviewed_body && <details><summary>Reviewed reply</summary><p dir="auto">{a.reviewed_body}</p></details>}</div><span className="tag gray">{labels[a.status] || a.status}</span></div>)}{!activity.length && <p className="meta">No decisions recorded yet.</p>}</div>
  </>;
}
