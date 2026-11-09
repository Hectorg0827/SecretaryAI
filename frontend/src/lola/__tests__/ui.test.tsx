import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { LolaOrb } from '../components/LolaOrb';
import { ActionCard, ReceiptView } from '../components/cards/Actions';
import { EvidenceCardView } from '../components/cards/Cards';
import { Composer, MessageList } from '../components/Conversation';
import { LolaDock } from '../components/LolaDock';
import { MockLolaTransport } from '../mockTransport';
import { setTransport, useLolaStore } from '../store';
import { useLolaPrefs } from '../settings';
import type { ActionPreview, LolaEvent, TurnRequest } from '../contract';
import type { LolaTransport as T } from '../transport';
import { loginAs } from './helpers';

const wrap = (ui: React.ReactNode, path = '/') => render(<MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>);

function resetStore() {
  useLolaStore.setState({ messages: [], conversationId: null, streamingId: null, workingStep: undefined, voiceState: 'off', mode: 'closed', language: 'en' });
  useLolaPrefs.setState({ language: 'auto', speakReplies: true, continuousConversation: false, transcriptRetention: 'session', audioRetention: 'none' });
}

beforeEach(() => {
  loginAs('owner');
  resetStore();
  setTransport(new MockLolaTransport({ wordDelayMs: 0, stepDelayMs: 1, outages: [] }));
});
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

const action: ActionPreview = {
  id: 'a1', action_type: 'send_collection_email', title: 'Send reminders', summary: 's', fields: [],
  risk: 'low', can_approve: true, status: 'proposed',
};

describe('LolaOrb', () => {
  it.each(['idle', 'listening', 'thinking', 'speaking', 'working', 'awaiting_approval', 'attention', 'error'] as const)('renders %s', (s) => {
    render(<LolaOrb state={s} label={`Lola ${s}`} />);
    const orb = screen.getByRole('img', { name: `Lola ${s}` });
    expect(orb.getAttribute('data-state')).toBe(s);
  });
  it('is hidden from assistive tech when unlabeled', () => {
    const { container } = render(<LolaOrb state="idle" />);
    expect(container.firstElementChild?.getAttribute('aria-hidden')).toBe('true');
  });
});

describe('ActionCard', () => {
  it('offers approval only when the server allows it', () => {
    const { rerender } = render(<ActionCard action={action} lang="en" onApprove={() => {}} onReject={() => {}} />);
    expect(screen.getByRole('button', { name: /approve & run/i })).toBeTruthy();
    rerender(<ActionCard action={{ ...action, can_approve: false, approval_note: 'Needs a manager.' }} lang="en" onApprove={() => {}} onReject={() => {}} />);
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull();
    expect(screen.getByText('Needs a manager.')).toBeTruthy();
  });
  it('high-risk actions need a second, explicit confirmation', () => {
    const onApprove = vi.fn();
    render(<ActionCard action={{ ...action, risk: 'high' }} lang="en" onApprove={onApprove} onReject={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: /review & approve/i }));
    expect(onApprove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /confirm/i }));
    expect(onApprove).toHaveBeenCalledOnce();
  });
  it('says "Done · verified" only for verified receipts', () => {
    const { rerender } = render(<ReceiptView lang="en" receipt={{ action_id: 'a', status: 'executed', verified: true, message: 'ok' }} />);
    expect(screen.getByText(/done · verified/i)).toBeTruthy();
    rerender(<ReceiptView lang="en" receipt={{ action_id: 'a', status: 'executed', verified: false, message: 'ok' }} />);
    expect(screen.queryByText(/done/i)).toBeNull();
    expect(screen.getByText(/awaiting confirmation/i)).toBeTruthy();
  });
});

describe('Evidence cards', () => {
  it('never renders values for unavailable data', () => {
    render(<EvidenceCardView card={{ id: 't', kind: 'table', title: 'Invoices', availability: 'unavailable', availability_note: 'QuickBooks down', columns: [{ key: 'a', label: 'A' }], rows: [{ a: '$999' }] }} />);
    expect(screen.getByText(/data unavailable/i)).toBeTruthy();
    expect(screen.queryByText('$999')).toBeNull();
  });
  it('charts include an accessible table view', () => {
    const { container } = render(<EvidenceCardView card={{ id: 'c', kind: 'chart', chart: 'line', title: 'Revenue', availability: 'ok', x_key: 'd', series: [{ key: 'v', label: 'Rev' }], data: [{ d: 'Mon', v: 1200 }], value_format: 'currency' }} />);
    expect(container.querySelector('table.sr-only caption')?.textContent).toBe('Revenue');
  });
});

describe('Conversation (owner, demo transport)', () => {
  it('AR workflow: ask → evidence → approve → verified receipt', async () => {
    wrap(<><MessageList /><Composer /></>);
    fireEvent.change(screen.getByLabelText(/ask lola anything/i), { target: { value: 'Which invoices are overdue?' } });
    fireEvent.keyDown(screen.getByLabelText(/ask lola anything/i), { key: 'Enter' });
    const reply = await screen.findByTestId('lola-reply');
    await waitFor(() => expect(reply.getAttribute('data-status')).toBe('complete'));
    expect(within(reply).getByText('INV-10442')).toBeTruthy();
    expect(within(reply).getByText(/QuickBooks Online · A\/R Aging/)).toBeTruthy();
    fireEvent.click(within(reply).getByRole('button', { name: /approve & run/i }));
    expect(await within(reply).findByText(/running… not confirmed yet/i)).toBeTruthy();
    expect(await within(reply).findByText(/done · verified/i, {}, { timeout: 3000 })).toBeTruthy();
  });

  it('Spanish question gets a Spanish answer', async () => {
    wrap(<><MessageList /><Composer /></>);
    await act(() => useLolaStore.getState().send('¿Qué facturas están vencidas?'));
    expect(screen.getByText(/Hay cinco facturas vencidas/)).toBeTruthy();
    expect(useLolaStore.getState().language).toBe('es');
  });

  it('interruption: a new question supersedes the running turn', async () => {
    const seen: TurnRequest[] = [];
    const slow: T = {
      kind: 'mock',
      async *turn(req: TurnRequest, signal: AbortSignal): AsyncGenerator<LolaEvent> {
        seen.push(req);
        yield { type: 'text', delta: `Answer to ${req.message}` };
        if (req.message === 'first') await new Promise((r) => signal.addEventListener('abort', r));
        else yield { type: 'done' };
      },
      approve: async () => { throw new Error(); }, reject: async () => {}, briefing: async () => { throw new Error(); },
    };
    setTransport(slow);
    wrap(<MessageList />);
    void useLolaStore.getState().send('first');
    await screen.findByText('Answer to first');
    await act(() => useLolaStore.getState().send('actually, second'));
    const replies = screen.getAllByTestId('lola-reply');
    expect(replies[0].getAttribute('data-status')).toBe('interrupted');
    expect(screen.getByText(/interrupted — nothing was executed/i)).toBeTruthy();
    expect(replies[1].getAttribute('data-status')).toBe('complete');
    expect(seen[1].supersedes_turn).toBe(true);
  });

  it('a dropped stream is shown as an error with retry, not a success', async () => {
    setTransport({
      kind: 'mock',
      async *turn(): AsyncGenerator<LolaEvent> { yield { type: 'text', delta: 'Partial' }; },
      approve: async () => { throw new Error(); }, reject: async () => {}, briefing: async () => { throw new Error(); },
    });
    wrap(<MessageList />);
    await act(() => useLolaStore.getState().send('hi'));
    expect(screen.getByRole('alert').textContent).toMatch(/nothing was executed/i);
    expect(screen.getByRole('button', { name: /try again/i })).toBeTruthy();
  });

  it('sales rep is refused company financials', async () => {
    loginAs('sales_rep');
    setTransport(new MockLolaTransport({ wordDelayMs: 0, stepDelayMs: 1, outages: [] }));
    wrap(<MessageList />);
    await act(() => useLolaStore.getState().send('What is our gross margin?'));
    expect(screen.getByText(/not available for your role/i)).toBeTruthy();
    expect(screen.queryByText(/31\.4%/)).toBeNull();
  });
});

describe('LolaDock', () => {
  it('opens with the launcher and Ctrl+K, closes with Esc and returns focus', async () => {
    wrap(<LolaDock />);
    fireEvent.keyDown(window, { key: 'k', ctrlKey: true });
    expect(screen.getByTestId('lola-panel')).toBeTruthy();
    fireEvent.keyDown(screen.getByTestId('lola-panel'), { key: 'Escape' });
    expect(screen.queryByTestId('lola-panel')).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByTestId('lola-launcher')));
  });
  it('is not rendered for viewers', () => {
    loginAs('viewer');
    wrap(<LolaDock />);
    expect(screen.queryByTestId('lola-launcher')).toBeNull();
  });
});

describe('Voice', () => {
  type Handler = ((e: unknown) => void) | null;
  class FakeRecognition {
    static last: FakeRecognition;
    lang = ''; interimResults = false; continuous = false; maxAlternatives = 1;
    onresult: Handler = null; onerror: Handler = null; onend: (() => void) | null = null;
    started = false;
    constructor() { FakeRecognition.last = this; }
    start() { this.started = true; }
    stop() { this.onend?.(); }
    abort() { this.onend?.(); }
    say(text: string, isFinal: boolean) {
      const r = Object.assign([{ transcript: text }], { isFinal });
      this.onresult?.({ resultIndex: 0, results: [r] });
    }
  }
  const spoken: string[] = [];
  beforeEach(() => {
    spoken.length = 0;
    (window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeRecognition;
    (window as unknown as Record<string, unknown>).SpeechSynthesisUtterance = class { text: string; onstart?: () => void; onend?: () => void; lang = ''; constructor(t: string) { this.text = t; } };
    (window as unknown as Record<string, unknown>).speechSynthesis = {
      speak: (u: { text: string; onstart?: () => void }) => { spoken.push(u.text); u.onstart?.(); },
      cancel: vi.fn(),
      getVoices: () => [],
    };
  });
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
    delete (window as unknown as Record<string, unknown>).speechSynthesis;
  });

  it('mic is off by default and listens only after an explicit tap; shows live transcript; sends as voice', async () => {
    wrap(<><MessageList /><Composer /></>);
    expect(screen.getByTestId('mic-button').getAttribute('aria-pressed')).toBe('false');
    fireEvent.click(screen.getByTestId('mic-button'));
    expect(FakeRecognition.last.started).toBe(true);
    expect(useLolaStore.getState().voiceState).toBe('listening');
    act(() => FakeRecognition.last.say('Where is container', false));
    expect(screen.getByTestId('live-transcript').textContent).toContain('Where is container');
    act(() => FakeRecognition.last.say('Where is container MSKU 448812?', true));
    act(() => FakeRecognition.last.stop());
    const reply = await screen.findByTestId('lola-reply');
    await waitFor(() => expect(reply.getAttribute('data-status')).toBe('complete'));
    expect(useLolaStore.getState().messages[0].channel).toBe('voice');
    await waitFor(() => expect(spoken.join(' ')).toContain('MSKU 448812'));
  });

  it('barge-in: tapping the mic while Lola speaks stops her immediately', async () => {
    wrap(<Composer />);
    act(() => useLolaStore.getState().setVoiceState('speaking'));
    const cancel = (window.speechSynthesis as unknown as { cancel: ReturnType<typeof vi.fn> }).cancel;
    fireEvent.click(screen.getByRole('button', { name: /^stop$/i }));
    expect(cancel).toHaveBeenCalled();
    expect(useLolaStore.getState().voiceState).toBe('off');
  });
});
