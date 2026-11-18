'use client';
import { useEffect, useRef, useState } from 'react';
import { api } from '../lib/api';

type Msg = { role: 'agent' | 'user'; text: string; summary?: any };

export default function ChatPage() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState('');
  const [autoPrice, setAutoPrice] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [digest, setDigest] = useState<any>(null);
  const [pendingNow, setPendingNow] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  async function loadChat() {
    setLoading(true);
    setLoadError('');
    setReady(false);
    try {
      const s = await api('/api/status');
      setPendingNow(s.pending_now ?? null);
      if (s.onboarding_done) {
        setDone(true);
        setMsgs([{ role: 'agent', text: "Setup is already saved. Ask me for today's summary, or open Campaigns from the menu." }]);
        api('/api/digest/latest').then(d => { if (d && !d.empty) setDigest(d); }).catch(() => {});
      } else {
        const o = await api('/api/onboarding');
        if (!o || !o.answers || (!o.next_question && !o.summary)) throw new Error('Invalid saved setup state');
        const saved: Msg[] = Object.entries(o.answers).map(([key, value]) => ({ role: 'user', text: key.charAt(0).toUpperCase() + key.slice(1) + ': ' + String(value) }));
        if (o.next_question) setMsgs([...saved, { role: 'agent', text: o.next_question[1] }]);
        else setMsgs([...saved, { role: 'agent', text: 'Review your saved setup and confirm the explicit rule.', summary: o.summary }]);
      }
      setReady(true);
      window.dispatchEvent(new Event('cra-clear-error'));
    } catch {
      setLoadError('Could not load your saved setup. Your answers have not been changed. Retry to resume the correct question.');
    } finally { setLoading(false); }
  }

  useEffect(() => {
    loadChat();
    const timer = setInterval(() => api('/api/status').then(s => setPendingNow(s.pending_now)).catch(() => {}), 15000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => { scrollRef.current?.scrollTo(0, 999999); }, [msgs, digest]);

  async function send(text: string) {
    if (!text.trim() || busy || !ready) return;
    setBusy(true);
    try {
    setMsgs((m) => [...m, { role: 'user', text }]);
    setInput('');
    const res = await api('/api/chat', { method: 'POST', body: JSON.stringify({ text }) });
    if (res.type === 'summary') {
      setMsgs((m) => [...m, { role: 'agent', text: res.message, summary: res.summary }]);
    } else if (res.type === 'confirmed') {
      setDone(true);
      setMsgs((m) => [...m, { role: 'agent', text: res.message }]);
    } else if (res.digest) {
      setDigest(res.digest);
      setPendingNow(res.digest.pending_now);
      setMsgs((m) => [...m, { role: 'agent', text: res.message }]);
    } else {
      setMsgs((m) => [...m, { role: 'agent', text: res.message || res.detail || '...' }]);
    }
    } catch {} finally { setBusy(false); }
  }

  async function confirm() {
    if (busy) return;
    setBusy(true);
    try { const r = await api('/api/onboarding/confirm', { method: 'POST', body: JSON.stringify({ auto_price: autoPrice }) }); if (r.ok) { setDone(true); setMsgs(m => [...m, { role: 'agent', text: r.message }]); } } catch {} finally { setBusy(false); }
  }

  return (
    <>
      <div className="page-title">Chat</div>
      <div className="page-sub">Set the business facts and explicit boundaries. Review drafts in the decision queue.</div>
      {loadError && <div className="notice" role="alert"><p>{loadError}</p><button className="btn primary" disabled={loading} onClick={loadChat}>{loading ? 'Retrying...' : 'Retry loading setup'}</button></div>}
      {loading && <p role="status">Loading saved setup...</p>}
      <div className="chat-box">
        <div className="chat-scroll" ref={scrollRef}>
          {msgs.map((m, i) =>
            m.summary ? (
              <div key={i} className="bubble agent summary-card card">
                <div style={{ fontWeight: 600 }}>{m.text}</div>
                <dl>
                  <dt>Business</dt><dd>{m.summary.business}</dd>
                  <dt>Allowed facts</dt><dd>{m.summary.facts}</dd>
                  <dt>Campaign</dt><dd>{m.summary.campaign}</dd>
                  <dt>Tone</dt><dd>{m.summary.tone}</dd>
                  <dt>Auto-reply boundaries</dt><dd>{m.summary.autonomy}</dd>
                </dl>
                {m.summary.limits_note && <div className="note" style={{ marginTop: 0, marginBottom: 10 }}>⚠️ {m.summary.limits_note}</div>}
                <div className="notice"><b>Effective rule: review only by default</b><p>Automatic permission is never inferred from your notes. Simple price-only replies require an exact recorded price and at least 85% confidence. Discounts and other questions always need review.</p></div>
                <label className="check-line"><input type="checkbox" checked={autoPrice} disabled={!m.summary.price || done || busy} onChange={e => setAutoPrice(e.target.checked)} /><span>I explicitly allow automatic price-only replies using {m.summary.price || 'an exact recorded price (not set)'}. Everything else needs my review.</span></label>
                <button className="btn primary" disabled={done || busy} onClick={confirm}>{done ? 'Summary confirmed' : 'Confirm summary and explicit rule'}</button>
              </div>
            ) : (
              <div key={i} className={'bubble ' + m.role}>{m.text}</div>
            )
          )}
          {digest && (
            <div className="card" style={{ alignSelf: 'flex-start', maxWidth: '100%' }}>
              <b>Today's summary ({digest.date}){digest.generated_at && <span style={{ fontWeight: 400, fontSize: 12, color: 'var(--ink2)' }}>. Snapshot generated {digest.generated_at}</span>}</b>
              <div className="chips" style={{ marginTop: 8 }}>
                <span className="chip">Human replies in snapshot: <b>{digest.arrived}</b></span>
                {(digest.classifications || []).map((c: any) => <span key={c.classification} className="chip">{{interested: 'Interested', question: 'Question', objection: 'Objection', not_now: 'Not now', opt_out: 'Stop request', unclear: 'Unclear'}[c.classification as string] || c.classification}: <b>{c.c}</b></span>)}
                {digest.auto_skipped > 0 && <span className="chip">Auto-replies skipped: <b>{digest.auto_skipped}</b></span>}
                {digest.replied_sent > 0 && <span className="chip ok">Gmail accepted: <b>{digest.replied_sent}</b></span>}
                {digest.replied_simulated > 0 && <span className="chip">Replied (simulation): <b>{digest.replied_simulated}</b></span>}
                {digest.send_failed > 0 && <span className="chip warn">Open outbound attempts now: <b>{digest.send_failed}</b></span>}
                {digest.followups_pending > 0 && <span className="chip">Follow-up requests open: <b>{digest.followups_pending}</b></span>}
                {digest.dnc_today > 0 && <span className="chip">Do not contact: <b>{digest.dnc_today}</b></span>}
              </div>
              <div className="chips" style={{ marginTop: 6 }}>
                <span className="chip warn">Awaiting your decision now: <b>{pendingNow ?? digest.pending_now}</b></span>
                <span style={{ fontSize: 11, color: 'var(--ink2)' }}>Live number, not from the summary snapshot</span>
              </div>
              {(digest.cases || []).length > 0 && (
                <div style={{ marginTop: 8, fontSize: 12, borderTop: '1px solid var(--line)', paddingTop: 8 }}>
                  {(digest.cases || []).slice(0, 6).map((c: any, i: number) => (
                    <div key={i} style={{ padding: '3px 0', color: 'var(--ink2)' }}>
                      <b style={{ color: 'var(--ink)' }}>{c.contact_name || c.contact_email}</b>
                      {' — '}{c.reason}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
        <div className="chat-input">
          <input aria-label="Chat message" disabled={busy || !ready} value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send(input)}
            placeholder={done ? 'Ask for today\'s summary…' : 'Type your answer…'} />
          <button disabled={busy || !ready || !input.trim()} onClick={() => send(input)}>{busy ? "Working..." : "Send"}</button>
        </div>
      </div>
    </>
  );
}
