/**
 * Real backend transport.
 *
 * Primary: Workstream 1's Lola orchestration endpoints (docs/lola/CONTRACT.md).
 * Fallback: existing SecretaryAI endpoints, so Lola is useful today. Every
 * fallback is honest: missing capabilities appear as notices / unavailable
 * sections, never as empty successes.
 */
import { api, API_BASE, type DashboardSummary, type Draft, type FeedEvent } from '../lib/api';
import type { Role } from '../hooks/useAuth';
import type {
  Attachment, Briefing, BriefingSection, LolaEvent, Receipt, TurnRequest, LolaLanguage,
} from './contract';
import { parseSse, type LolaTransport } from './transport';
import { canApproveActions, canSeeFinancials, filterBriefingForRole } from './permissions';
import { greeting } from './persona';

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('secretary_token');
  return token ? { Authorization: `Bearer ${token}` } : {};
}

const KNOWN_EVENT_TYPES = new Set([
  'conversation', 'status', 'text', 'language', 'card', 'action_preview',
  'receipt', 'notice', 'suggestions', 'error', 'done',
]);

/** Map a legacy `/api/chat/message` payload onto contract events. */
export function mapLegacyPayload(p: Record<string, unknown>, seenConversation: { id?: string }): LolaEvent[] {
  const out: LolaEvent[] = [];
  const cid = typeof p.conversation_id === 'string' ? p.conversation_id : undefined;
  if (cid && seenConversation.id !== cid) {
    seenConversation.id = cid;
    out.push({ type: 'conversation', conversation_id: cid });
  }
  if (typeof p.text === 'string' && p.text) out.push({ type: 'text', delta: p.text });
  if (p.action && typeof p.action === 'object') {
    // Legacy detection is keyword-based and has no server draft id, so it
    // cannot be approved from chat. Surface it as a suggestion, not a button.
    out.push({
      type: 'notice',
      notice: {
        kind: 'info',
        message: 'Lola suggested an action. Create it from the Work page so it goes through approval.',
      },
    });
  }
  if (p.done === true) out.push({ type: 'done' });
  return out;
}

async function* legacyTurn(req: TurnRequest, signal: AbortSignal): AsyncGenerator<LolaEvent> {
  if (req.attachments?.length) {
    yield {
      type: 'notice',
      notice: { kind: 'data_unavailable', message: 'Attachments need the Lola service, which isn’t enabled on this server yet. I answered from your question only.' },
    };
  }
  const res = await fetch(`${API_BASE}/api/chat/message`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ message: req.message, conversation_id: req.conversation_id }),
    signal,
  });
  yield* handleStream(res, signal, true);
}

async function* handleStream(res: Response, signal: AbortSignal, legacy: boolean): AsyncGenerator<LolaEvent> {
  if (res.status === 403) {
    const detail = await res.json().catch(() => ({} as Record<string, unknown>));
    yield {
      type: 'notice',
      notice: { kind: 'permission_denied', message: String((detail as { detail?: string }).detail ?? 'Your role doesn’t have access to this.') },
    };
    yield { type: 'done' };
    return;
  }
  if (res.status === 401) {
    yield { type: 'error', message: 'Your session expired. Please sign in again.', retryable: false };
    return;
  }
  if (!res.ok || !res.body) {
    yield { type: 'error', message: `Lola couldn’t reach the server (HTTP ${res.status}). Nothing was executed.`, retryable: true };
    return;
  }

  const seen: { id?: string } = {};
  let finished = false;
  for await (const payload of parseSse(res.body, signal)) {
    if (!payload || typeof payload !== 'object') continue;
    const p = payload as Record<string, unknown>;
    const events: LolaEvent[] = legacy || !KNOWN_EVENT_TYPES.has(String(p.type))
      ? mapLegacyPayload(p, seen)
      : [p as unknown as LolaEvent];
    for (const ev of events) {
      if (ev.type === 'done') finished = true;
      yield ev;
    }
  }
  if (!finished && !signal.aborted) {
    // The stream died before the server said it was finished. Never let a
    // partial answer read as a complete one.
    yield {
      type: 'error',
      message: 'The connection dropped before I finished. What you see may be incomplete, and nothing was executed.',
      retryable: true,
    };
  }
}

export class HttpLolaTransport implements LolaTransport {
  readonly kind = 'http' as const;
  private lolaAvailable: boolean | null = null;

  async *turn(req: TurnRequest, signal: AbortSignal): AsyncGenerator<LolaEvent> {
    try {
      if (this.lolaAvailable !== false) {
        const res = await fetch(`${API_BASE}/api/lola/turn`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream', ...authHeaders() },
          body: JSON.stringify(req),
          signal,
        });
        if (res.status === 404 || res.status === 405) {
          this.lolaAvailable = false;
        } else {
          this.lolaAvailable = true;
          yield* handleStream(res, signal, false);
          return;
        }
      }
      yield* legacyTurn(req, signal);
    } catch (err) {
      if (signal.aborted) return;
      yield {
        type: 'error',
        message: 'I lost the connection to Lion BI. Nothing was executed — try again when you’re back online.',
        retryable: true,
      };
      void err;
    }
  }

  async extractFile(file: File): Promise<Attachment | null> {
    const form = new FormData();
    form.append('file', file, file.name);
    const res = await fetch(`${API_BASE}/api/lola/files`, { method: 'POST', headers: authHeaders(), body: form })
      .catch(() => null);
    if (!res || res.status === 404 || res.status === 405) return null; // older server: no extraction
    if (!res.ok) {
      const body = await res.json().catch(() => ({} as Record<string, unknown>));
      throw new Error(typeof body.detail === 'string' ? body.detail : `${file.name} couldn't be read.`);
    }
    const body = await res.json() as { name: string; mime: string; size: number; text: string };
    return { name: body.name, size: file.size, mime: body.mime, text: body.text };
  }

  async approve(actionId: string): Promise<Receipt> {
    const res = await fetch(`${API_BASE}/api/lola/actions/${encodeURIComponent(actionId)}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: '{}',
    }).catch(() => null);
    if (res && res.ok) return (await res.json()) as Receipt;
    if (res && res.status !== 404 && res.status !== 405) {
      const d = await res.json().catch(() => ({}));
      throw new Error((d as { detail?: string }).detail ?? `Approval failed (HTTP ${res.status})`);
    }
    // Legacy approval queue. It executes server-side but doesn't return a
    // verified receipt, so we must not claim completion.
    const r = await api.post<Record<string, unknown>>(`/api/actions/approve/${encodeURIComponent(actionId)}`, {});
    return {
      action_id: actionId,
      status: 'submitted',
      verified: false,
      message: r?.already_processed
        ? `This was already ${String(r.status ?? 'processed')}. I didn’t run it again.`
        : 'Approved and handed to the execution queue. I’ll confirm once the system of record shows it — check the Audit Log for the result.',
    };
  }

  async getReceipt(actionId: string): Promise<Receipt | null> {
    const res = await fetch(`${API_BASE}/api/lola/actions/${encodeURIComponent(actionId)}`, { headers: authHeaders() })
      .catch(() => null);
    if (!res || res.status === 404 || res.status === 405) return null;
    if (!res.ok) throw new Error(`Receipt check failed (HTTP ${res.status})`);
    const body = await res.json() as { client_receipt?: Receipt };
    return body.client_receipt ?? null;
  }

  async reject(actionId: string, reason?: string): Promise<void> {
    const res = await fetch(`${API_BASE}/api/lola/actions/${encodeURIComponent(actionId)}/reject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ reason }),
    }).catch(() => null);
    if (res && res.ok) return;
    await api.actions.reject(actionId, reason);
  }

  async briefing(role: Role, signal?: AbortSignal): Promise<Briefing> {
    const res = await fetch(`${API_BASE}/api/lola/briefing`, { headers: authHeaders(), signal }).catch(() => null);
    if (res && res.ok) return filterBriefingForRole((await res.json()) as Briefing, role);
    return filterBriefingForRole(await composeLegacyBriefing(role), role);
  }
}

function unavailable(id: BriefingSection['id'], title: string, note: string): BriefingSection {
  return { id, title, availability: 'unavailable', availability_note: note, items: [] };
}

/**
 * Build a briefing from existing endpoints the role may call. Sections whose
 * source fails are marked `unavailable` — never shown as "all clear".
 */
export async function composeLegacyBriefing(role: Role, lang: LolaLanguage = 'en'): Promise<Briefing> {
  const fin = canSeeFinancials(role);
  const approver = canApproveActions(role);
  const settle = <T,>(p: Promise<T>) => p.then((v) => ({ ok: true as const, v }), (e: unknown) => ({ ok: false as const, e }));

  const [summary, feed, pending] = await Promise.all([
    settle(api.dashboard.summary()),
    settle(api.feed.list(true)),
    approver ? settle(api.actions.pending()) : Promise.resolve(null),
  ]);

  const sections: BriefingSection[] = [];
  const now = new Date().toISOString();

  if (feed.ok) {
    const events = (feed.v as { events: FeedEvent[] }).events ?? [];
    const high = events.filter((e) => e.priority === 'high');
    sections.push({
      id: 'critical_alerts', title: 'Critical alerts', availability: 'ok',
      items: high.slice(0, 5).map((e) => ({ id: e.id, title: e.title, detail: e.body, severity: 'critical', ask: `Tell me more about: ${e.title}` })),
      evidence: [{ source: 'Lion BI proactive feed', as_of: now }],
    });
    sections.push({
      id: 'next_actions', title: 'Suggested next actions', availability: 'ok',
      items: events.filter((e) => e.action_label).slice(0, 5).map((e) => ({ id: `na-${e.id}`, title: e.action_label ?? e.title, detail: e.entity_name ?? undefined, ask: `${e.action_label} — ${e.title}` })),
    });
  } else {
    sections.push(unavailable('critical_alerts', 'Critical alerts', 'Alerts feed is unreachable right now.'));
    sections.push(unavailable('next_actions', 'Suggested next actions', 'Alerts feed is unreachable right now.'));
  }

  if (summary.ok) {
    const s = summary.v as DashboardSummary;
    const stale = s.generated_at && Date.now() - new Date(s.generated_at).getTime() > 6 * 3600_000;
    sections.push({
      id: 'department_priorities', title: 'Department priorities',
      // Account health can be "not assessed" (null): never show that as zero risk.
      availability: stale ? 'stale' : s.accounts ? 'ok' : 'partial',
      availability_note: stale
        ? `Data last refreshed ${new Date(s.generated_at!).toLocaleString()}.`
        : s.accounts ? undefined : 'Account health has not been assessed yet.',
      items: [
        ...(s.inventory_alerts ?? []).slice(0, 4).map((a) => ({
          id: `inv-${a.item_id}`, title: `${a.product_name}: ${a.stock_status.replace(/_/g, ' ')}`,
          detail: a.weeks_remaining != null ? `${a.weeks_remaining} weeks of stock left` : undefined,
          severity: (a.stock_status === 'low' ? 'warning' : 'critical') as 'warning' | 'critical',
          href: '/inventory', ask: `What should we do about ${a.product_name}?`,
        })),
        ...(s.accounts && s.accounts.at_risk > 0 ? [{ id: 'acc-risk', title: `${s.accounts.at_risk} accounts at risk`, severity: 'warning' as const, href: '/accounts', ask: 'Which accounts are at risk and why?' }] : []),
      ],
      evidence: [{ source: 'QuickBooks snapshot', as_of: s.generated_at }],
    });
  } else {
    sections.push(unavailable('department_priorities', 'Department priorities', 'Business data is unreachable — check Connectors.'));
  }

  if (approver && pending) {
    sections.push(pending.ok
      ? {
          id: 'pending_approvals', title: 'Pending approvals', availability: 'ok',
          items: ((pending.v as { drafts: Draft[] }).drafts ?? []).slice(0, 6).map((d) => ({
            id: d.id, title: d.action_type.replace(/_/g, ' '), meta: new Date(d.created_at).toLocaleString(), href: '/work',
          })),
        }
      : unavailable('pending_approvals', 'Pending approvals', 'Approval queue is unreachable.'));
  }

  // Sections the legacy API cannot produce — say so explicitly.
  const needsLola = 'Available once the Lola briefing service is enabled.';
  sections.push(unavailable('morning_briefing', 'Morning briefing', needsLola));
  if (fin) {
    sections.push({ ...unavailable('performance', 'Business performance', needsLola), sensitivity: 'financial' });
    sections.push({ ...unavailable('financial_risks', 'Financial risks', needsLola), sensitivity: 'financial' });
  }
  sections.push(unavailable('logistics_exceptions', 'Logistics exceptions', needsLola));
  sections.push(unavailable('recent_tasks', 'Recent completed tasks', needsLola));

  return {
    generated_at: now,
    greeting: greeting(lang),
    headline: lang === 'es' ? 'Esto es lo que puedo ver ahora mismo.' : 'Here’s what I can see right now.',
    sections,
  };
}
