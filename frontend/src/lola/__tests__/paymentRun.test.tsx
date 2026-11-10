import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ReceiptView } from '../components/cards/Actions';
import { setTransport } from '../store';
import type { LolaTransport } from '../transport';

const approved = {
  action_id: 'rcpt-12345678', status: 'executed' as const, verified: true,
  message: 'Payment run approved. Nothing was paid.', reference: 'lion-bi:payment-run:draft-1',
};

function transport(over: Partial<LolaTransport>): LolaTransport {
  return { turn: async function* () { /* unused */ }, approve: vi.fn(), reject: vi.fn(),
    briefing: vi.fn(), ...over } as unknown as LolaTransport;
}

describe('approved supplier payment run', () => {
  afterEach(() => vi.restoreAllMocks());

  it('says "nothing paid", never "done", and offers the file', async () => {
    const downloadPaymentRun = vi.fn(async () => {});
    setTransport(transport({ downloadPaymentRun }));
    render(<ReceiptView receipt={approved} lang="en" />);
    expect(screen.getByText(/Approved · nothing paid/)).toBeTruthy();
    expect(screen.queryByText(/Done · verified/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /download payment file/i }));
    await waitFor(() => expect(downloadPaymentRun).toHaveBeenCalledWith('rcpt-12345678'));
  });

  it('shows the server reason when the download is refused', async () => {
    setTransport(transport({ downloadPaymentRun: async () => { throw new Error('Only leadership or finance can download payment files.'); } }));
    render(<ReceiptView receipt={approved} lang="es" />);
    expect(screen.getByText(/no se pagó nada/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /archivo de pagos/i }));
    expect((await screen.findByRole('alert')).textContent).toMatch(/leadership or finance/);
  });

  it('ordinary verified receipts have no payment button', () => {
    setTransport(transport({ downloadPaymentRun: vi.fn() }));
    render(<ReceiptView receipt={{ ...approved, reference: 'email:abc' }} lang="en" />);
    expect(screen.getByText(/Done · verified/)).toBeTruthy();
    expect(screen.queryByRole('button')).toBeNull();
  });
});
