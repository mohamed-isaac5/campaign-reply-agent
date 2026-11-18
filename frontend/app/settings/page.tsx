'use client';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

export default function SettingsPage() {
  const [status, setStatus] = useState<any>(null);
  const [test, setTest] = useState<any>(null);
  const [ob, setOb] = useState<any>(null);
  const [drafts, setDrafts] = useState<any>({});
  const [auto, setAuto] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editMsg, setEditMsg] = useState('');
  const loadOb = async () => { try { const r = await api('/api/onboarding'); setOb(r); setDrafts({ ...r.summary }); setAuto(r.rules?.version === 2 && r.rules?.auto_price === true); } catch {} };
  const loadStatus = () => api('/api/status').then(setStatus).catch(() => {});
  useEffect(() => { loadStatus(); loadOb(); const t = setInterval(loadStatus, 15000); return () => clearInterval(t); }, []);
  const labels: Record<string, string> = { business: 'What you sell and to whom', facts: 'Facts you may share', price: 'Exact fixed price and currency', campaign: 'Campaign plan', tone: 'Tone', autonomy: 'Your original permission notes (not an execution grant)' };
  if (!status) return <div className="empty">Loading local status...</div>;
  const health = status.worker_health;
  const stale = health && Date.now()/1000 - (health.at_unix || 0) > 120;
  return <>
    <div className="eyebrow">OPERATING CONTROLS</div>
    <h1 className="page-title">Settings & boundaries</h1>
    <p className="page-sub">The effective rule is explicit, not inferred from a sentence. Review it before enabling anything.</p>
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="section-heading"><h2>Effective automatic rule</h2><span className={'tag ' + (ob?.rules?.auto_price ? '' : 'gray')}>{ob?.rules?.version === 2 && ob?.rules?.auto_price ? 'Price-only enabled' : 'Review only'}</span></div>
      <div className="rules-grid"><div><small>RECORDED PRICE</small><b>{ob?.summary?.price || 'No fixed price'}</b></div><div><small>CONFIDENCE REQUIRED</small><b>85% or higher</b></div><div><small>ALWAYS NEEDS REVIEW</small><b>Discounts, mixed questions, unknown facts</b></div></div>
      <p className="meta">Opt-outs, campaign pause and the kill switch block sending, including manually approved drafts. An uncertain attempt is never retried automatically. Reply text is not a command.</p>
      {ob?.done ? <>
        <label className="check-line"><input type="checkbox" checked={auto} disabled={busy || !ob.summary.price} onChange={e => setAuto(e.target.checked)} /><span>I explicitly allow automatic replies to simple price-only questions using the recorded price. Everything else needs my review.</span></label>
        {!ob.summary.price && <div className="notice">Record a valid fixed price with currency first, for example 49.99 USD. Bare amounts and ranges are not accepted.</div>}
        <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); try { const r = await api('/api/campaigns/' + ob.campaign_id + '/rules', { method: 'POST', body: JSON.stringify({ auto_price: auto }) }); setEditMsg(r.ok ? 'Explicit rule saved. The effective summary is updated.' : r.detail); await loadOb(); } catch {} finally { setBusy(false); } }}>Save explicit rule</button>
      </> : <p className="meta">Finish onboarding in Chat to save a campaign and choose its rule.</p>}
      {editMsg && <div className="receipt" role="status">{editMsg}</div>}
    </div>
    <div className="card" style={{ marginBottom: 16 }}>
      <h2>Connections & worker health</h2>
      <div className="connection-row"><div><b>AI model</b><p>{status.llm.mode === 'invalid' ? status.llm.error : status.llm.mode === 'demo' ? 'Offline deterministic simulation. No real AI analysis is running.' : 'Configured provider: ' + status.llm.provider + '. Reply text is shared with this provider. Live behavior is not independently verified.'}</p></div><span className="tag gray">{status.llm.mode === 'invalid' ? 'Configuration error: model requests blocked' : status.llm.mode === 'demo' ? 'Demo' : 'Configured, not production-verified'}</span></div>
      <p className="meta">For a real model, use your local .env: LLM_PROVIDER and LLM_API_KEY. Never paste persistent secrets in chat. Use a test inbox before real campaign traffic.</p>
      <button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { setTest(await api('/api/llm/test', { method: 'POST', body: '{}' })); } catch {} finally { setBusy(false); } }}>Test configured model connection</button>
      {test && <div className={'notice ' + (test.ok ? '' : 'error')} role="status">{test.detail}</div>}
      <div className="connection-row"><div><b>Gmail</b><p>{status.gmail.note}</p></div><span className="tag gray">{status.gmail.connected ? 'Credentials present, not live-tested' : 'Disconnected'}</span></div>
      <p className="meta">Uses your own Google Cloud app and local token. The implementation has fake-contract tests, not a real-account end-to-end test. Follow README setup before starting a worker.</p>
      <div className={'notice ' + (health && (!health.ok || stale) ? 'error' : '')}><b>Worker: {health ? health.ok && !stale ? 'Last cycle healthy' : 'Needs attention' : 'Not started / no heartbeat'}</b><div>{health ? 'Last update: ' + health.at : 'No live email monitoring is running in this demo.'}</div>{health?.error && <p>{health.error}</p>}{health && <div>Consecutive failures: {health.consecutive_failures}</div>}<div>Open inbound work: {status.inbound_work_open}</div><div>Local processing: {status.pipeline_health?.ok === false ? "Needs attention: " + status.pipeline_health.error : "Ready / no reported error"}</div><div>Unresolved outbound attempts: {status.unresolved_sends}</div></div>
    </div>
    {ob?.done && <div className="card" style={{ marginBottom: 16 }}><h2>Business & campaign facts</h2><p className="meta">Changing any answer turns automatic permission off until you explicitly confirm it again above. Text is never interpreted as a new grant.</p>{['business', 'facts', 'price', 'campaign', 'tone', 'autonomy'].map(k => <div key={k} className="settings-field"><label className="field-label" htmlFor={'setting-' + k}>{labels[k]}</label><div className="input-action"><input id={'setting-' + k} className="input" value={drafts[k] ?? ''} onChange={e => setDrafts({ ...drafts, [k]: e.target.value })} /><button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { const r = await api('/api/onboarding/edit', { method: 'POST', body: JSON.stringify({ key: k, value: drafts[k] ?? '' }) }); setEditMsg(r.ok ? 'Saved. Automatic permission is now off; reconfirm it above if appropriate.' : r.detail); if (r.ok) loadOb(); } catch {} finally { setBusy(false); } }}>Save</button></div></div>)}</div>}
    <div className="card"><h2>MCP interface</h2><p className="meta">The local stdio server in backend/mcp_server.py exposes campaigns, review queue, daily summary and conversation details. It does not grant permission to send replies.</p></div>
  </>;
}
