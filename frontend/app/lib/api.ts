export async function api(path: string, init?: RequestInit) {
  try {
    const r = await fetch(path, { ...init, headers: { 'content-type': 'application/json', ...(init?.headers || {}) } });
    const data = await r.json();
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'The local server rejected the request. Nothing has been assumed successful.');
    if (data?.ok === false || data?.error) {
      window.dispatchEvent(new CustomEvent('cra-error', { detail: data.detail || data.error }));
    }
    if (init?.method === 'POST') { window.dispatchEvent(new Event('cra-refresh')); if (data?.ok !== false && !data?.error) window.dispatchEvent(new Event('cra-clear-error')); }
    return data;
  } catch (e: any) {
    window.dispatchEvent(new CustomEvent('cra-error', { detail: e.message || 'The local server could not be reached. Check the result before retrying.' }));
    throw e;
  }
}
export const INTENT_LABELS: Record<string, string> = {
  interested: 'Interested', question: 'Question', objection: 'Objection', not_now: 'Not now',
  opt_out: 'Stop request', auto_reply: 'Auto-reply skipped', unclear: 'Unclear', inbound_context: 'Historical context only', outbound_context: 'Our earlier message', processing: 'Processing',
};
export const ACTION_LABELS: Record<string, string> = {
  reply: 'Auto-reply', draft: 'Draft for review', dnc: 'Do not contact', note: 'Follow-up note', escalation: 'Escalation',
};
