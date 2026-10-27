import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fixtureBriefing } from './briefingFixture.test-data';

const mocks = vi.hoisted(() => ({
  today: vi.fn(), role: 'owner',
}));

vi.mock('../../lib/api', () => ({
  api: { briefing: { today: mocks.today } },
}));
vi.mock('../../hooks/useAuth', () => ({ useAuth: () => ({ role: mocks.role }) }));

import { MorningBriefing } from './MorningBriefing';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><MorningBriefing /></MemoryRouter>);
  });
  await act(async () => { await Promise.resolve(); });
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.role = 'owner';
  mocks.today.mockResolvedValue(fixtureBriefing());
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('canonical six-section executive briefing (synthetic API-only)', () => {
  it('uses the sole canonical endpoint and labels incomplete source coverage explicitly', async () => {
    const node = await render();
    expect(node.querySelectorAll('section[aria-label]')).toHaveLength(6);
    const body = node.textContent ?? '';
    expect(body).toContain('1 at-risk accounts');
    expect(body).toContain('2 slowing accounts');
    expect(body).toContain('1200.00');
    expect(body).toContain('Partial briefing — not a complete daily digest');
    expect(body).toContain('source_quality: partial');
    expect(body).toContain('complete_digest: false');
    expect(body).toContain('Opportunity sources are not covered');
    expect(body).toContain('No verified execution receipts are covered');
    expect(body).not.toContain('Synthetic opportunity');
    expect(mocks.today).toHaveBeenCalledTimes(1);
  });

  it('reports 503 as source unavailable, never zero activity or a clean bill of health', async () => {
    mocks.today.mockRejectedValue(new Error('HTTP 503 synthetic stale snapshot'));
    const node = await render();
    expect(node.querySelector('[role="alert"]')?.textContent).toContain('Canonical briefing unavailable');
    expect(node.querySelector('[aria-label="4 · Scoreboard"]')?.textContent).toContain('Source unavailable');
    expect(node.querySelector('[aria-label="1 · Red flags"]')?.textContent).toContain('Source unavailable');
    expect(node.textContent).not.toContain('0 at-risk accounts');
  });

  it('rejects malformed or self-promoted verified HTTP 200 responses', async () => {
    mocks.today.mockResolvedValue({ ...fixtureBriefing(), complete_digest: true, source_quality: 'verified' });
    const node = await render();
    expect(node.querySelector('[role="alert"]')?.textContent).toContain('Canonical briefing unavailable');
    expect(node.textContent).not.toContain('Last 30-day sales in snapshot');
  });

  it('refuses data projected for a different role', async () => {
    mocks.today.mockResolvedValue(fixtureBriefing('manager'));
    const node = await render();
    expect(node.querySelector('[role="alert"]')?.textContent).toContain('Canonical briefing unavailable');
    expect(node.textContent).not.toContain('1 at-risk accounts');
  });

  it('supports a read-only retry after source failure', async () => {
    mocks.today.mockRejectedValueOnce(new Error('503')).mockResolvedValueOnce(fixtureBriefing());
    const node = await render();
    const retry = [...node.querySelectorAll('button')].find((button) => button.textContent === 'Retry briefing read');
    expect(retry).toBeTruthy();
    await act(async () => { retry?.click(); });
    expect(mocks.today).toHaveBeenCalledTimes(2);
    expect(node.textContent).toContain('1 at-risk accounts');
  });
});
