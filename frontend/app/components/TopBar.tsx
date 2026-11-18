'use client';
import { useEffect, useState } from 'react';
import { api } from '../lib/api';

export default function TopBar() {
  const [status, setStatus] = useState<any>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const load = () => api('/api/status').then(setStatus).catch(() => {});
  useEffect(() => {
    load(); const t = setInterval(load, 15000);
    const notify = (event: Event) => setError((event as CustomEvent).detail);
    const clear = () => setError('');
    window.addEventListener('cra-clear-error', clear);
    window.addEventListener('cra-error', notify);
    window.addEventListener('cra-refresh', load);
    return () => { clearInterval(t); window.removeEventListener('cra-clear-error', clear); window.removeEventListener('cra-error', notify); window.removeEventListener('cra-refresh', load); };
  }, []);
  const kill = status?.kill_switch === 'on';
  return <>
    {error && <div className="error-banner" role="alert"><div><b>Action needs attention</b><div>{error}</div></div><button className="btn" onClick={() => setError('')}>Dismiss</button></div>}
    {kill && <div className="kill-banner">Sending is stopped. Classification and recording continue. Manual approval cannot bypass this switch.</div>}
    <div className="topbar">
      <span className="badge demo">{!status ? 'Loading local status...' : status.llm.mode === 'invalid' ? 'MODEL CONFIGURATION ERROR / requests blocked' : status.llm.mode === 'demo' ? 'SIMULATION / no real AI' : 'Configured model: ' + status.llm.provider}</span>
      <span className="badge">Gmail: {status?.gmail.connected ? 'credentials present, not live-tested' : 'disconnected'}</span>
      <span className="spacer" />
      <button disabled={busy || !status} className={'kill' + (kill ? ' on' : '')} onClick={async () => { setBusy(true); try { const r = await api('/api/kill-switch', { method: 'POST', body: JSON.stringify({ on: !kill }) }); if (r.kill_switch) { await load(); window.dispatchEvent(new Event('cra-refresh')); } } catch {} finally { setBusy(false); } }}>{busy ? 'Updating...' : kill ? 'Resume sending' : 'Stop all sending'}</button>
    </div>
  </>;
}
