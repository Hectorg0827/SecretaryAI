import { afterEach, describe, expect, it } from 'vitest';
import { detectLanguage } from '../persona';
import { filterBriefingForRole, guardAction, guardCard, SECTIONS_BY_ROLE } from '../permissions';
import { MockLolaTransport, classify } from '../mockTransport';
import { deriveOrbState, type LolaMessage } from '../store';
import type { ActionPreview, Briefing, EvidenceCard, LolaEvent } from '../contract';

describe('language detection', () => {
  it.each([
    ['¿Qué está pasando con mi negocio?', 'es'],
    ['Muéstrame las facturas vencidas', 'es'],
    ['What is happening with my business?', 'en'],
    ['Show me the income report', 'en'], // "income" must not count as Spanish "como"
    ['ok', 'en'],
  ])('%s → %s', (text, lang) => {
    expect(detectLanguage(text, 'en')).toBe(lang);
  });
  it('keeps the current language when ambiguous', () => {
    expect(detectLanguage('MSKU 448812', 'es')).toBe('es');
  });
});

const card: EvidenceCard = { id: 'c', kind: 'metrics', title: 'Revenue', availability: 'ok', sensitivity: 'financial', metrics: [{ label: 'Revenue', value: '$1M' }] };

describe('role guards', () => {
  it('hides financial cards from sales reps without leaking values', () => {
    const g = guardCard(card, 'sales_rep');
    expect(g.availability).toBe('forbidden');
    expect(JSON.stringify(g)).not.toContain('$1M');
    expect(guardCard(card, 'owner')).toBe(card);
  });
  it('defers approval rights to the server, but never to viewers', () => {
    const a = { id: 'a', can_approve: true } as ActionPreview;
    expect(guardAction(a, 'back_office').can_approve).toBe(true);
    expect(guardAction(a, 'viewer').can_approve).toBe(false);
    expect(guardAction({ ...a, can_approve: false }, 'owner').can_approve).toBe(false);
  });
  it('filters briefing sections by role', () => {
    const all: Briefing = {
      generated_at: '', greeting: '', headline: '',
      sections: SECTIONS_BY_ROLE.owner.map((id) => ({ id, title: id, availability: 'ok', items: [], sensitivity: id === 'performance' || id === 'financial_risks' ? 'financial' : 'standard' })),
    };
    const rep = filterBriefingForRole(all, 'sales_rep').sections.map((s) => s.id);
    expect(rep).not.toContain('financial_risks');
    expect(rep).not.toContain('performance');
    expect(rep).not.toContain('critical_alerts');
    expect(filterBriefingForRole(all, 'viewer').sections).toHaveLength(0);
    expect(filterBriefingForRole(all, 'owner').sections).toHaveLength(9);
  });
});

async function run(t: MockLolaTransport, message: string): Promise<LolaEvent[]> {
  const out: LolaEvent[] = [];
  for await (const e of t.turn({ message, language: 'en', channel: 'text' }, new AbortController().signal)) out.push(e);
  return out;
}

describe('mock transport scenarios', () => {
  afterEach(() => localStorage.clear());
  it('classifies the acceptance workflows', () => {
    expect(classify('What’s happening with my business?')).toBe('briefing');
    expect(classify('Which invoices are overdue?')).toBe('ar');
    expect(classify('Why did sales drop last week?')).toBe('sales');
    expect(classify('Where is container MSKU 448812?')).toBe('container');
    expect(classify('Any receiving exceptions today?')).toBe('warehouse');
    expect(classify('¿Qué facturas están vencidas?')).toBe('ar');
  });
  it('denies company financials to a sales rep', async () => {
    const ev = await run(new MockLolaTransport({ wordDelayMs: 0, stepDelayMs: 0, role: () => 'sales_rep' }), 'What is our gross margin?');
    expect(ev.find((e) => e.type === 'notice')).toMatchObject({ notice: { kind: 'permission_denied' } });
    expect(ev.some((e) => e.type === 'card')).toBe(false);
  });
  it('reports a connector outage with empty, unavailable cards', async () => {
    const ev = await run(new MockLolaTransport({ wordDelayMs: 0, stepDelayMs: 0, role: () => 'owner', outages: ['QuickBooks'] }), 'Which invoices are overdue?');
    expect(ev.find((e) => e.type === 'notice')).toMatchObject({ notice: { kind: 'connector_outage' } });
    const cards = ev.filter((e): e is Extract<LolaEvent, { type: 'card' }> => e.type === 'card');
    expect(cards.length).toBeGreaterThan(0);
    for (const c of cards) {
      expect(c.card.availability).toBe('unavailable');
      if (c.card.kind === 'table') expect(c.card.rows).toHaveLength(0);
    }
    expect(ev.some((e) => e.type === 'text')).toBe(false);
    expect(ev.some((e) => e.type === 'action_preview')).toBe(false);
  });
  it('only verified receipts are "executed + verified"', async () => {
    const t = new MockLolaTransport({ wordDelayMs: 0, stepDelayMs: 0, role: () => 'owner' });
    await run(t, 'Which invoices are overdue?');
    expect(await t.approve('act-ar-reminders')).toMatchObject({ status: 'executed', verified: true });
    await run(t, 'Any receiving exceptions today?');
    expect(await t.approve('act-vendor-claim')).toMatchObject({ status: 'submitted', verified: false });
  });
});

const msg = (p: Partial<LolaMessage>): LolaMessage => ({
  id: 'm', role: 'lola', text: '', language: 'en', channel: 'text', status: 'complete',
  cards: [], actions: [], receipts: [], notices: [], suggestions: [], startedAt: 0, ...p,
});

describe('orb state', () => {
  const base = { messages: [] as LolaMessage[], streamingId: null as string | null, voiceState: 'off' as const, workingStep: undefined as string | undefined };
  it('maps every state', () => {
    expect(deriveOrbState(base)).toBe('idle');
    expect(deriveOrbState(base, false)).toBe('error');
    expect(deriveOrbState({ ...base, voiceState: 'listening' as never })).toBe('listening');
    expect(deriveOrbState({ ...base, voiceState: 'speaking' as never })).toBe('speaking');
    expect(deriveOrbState({ ...base, streamingId: 'm', messages: [msg({ status: 'streaming' })] })).toBe('thinking');
    expect(deriveOrbState({ ...base, streamingId: 'm', workingStep: 'x', messages: [msg({ status: 'streaming' })] })).toBe('working');
    expect(deriveOrbState({ ...base, messages: [msg({ actions: [{ status: 'proposed', can_approve: true } as ActionPreview] })] })).toBe('awaiting_approval');
    expect(deriveOrbState({ ...base, messages: [msg({ actions: [{ status: 'executing' } as ActionPreview] })] })).toBe('working');
    expect(deriveOrbState({ ...base, messages: [msg({ notices: [{ kind: 'connector_outage', message: '' }] })] })).toBe('attention');
    expect(deriveOrbState({ ...base, messages: [msg({ status: 'error' })] })).toBe('error');
  });
});
