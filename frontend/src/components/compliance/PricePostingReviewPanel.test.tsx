// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listPrograms: vi.fn(),
  recordHumanReview: vi.fn(),
  role: 'owner',
}));
vi.mock('../../lib/api', () => ({
  api: { pricePosting: {
    listPrograms: mocks.listPrograms,
    recordHumanReview: mocks.recordHumanReview,
  } },
}));
vi.mock('../../hooks/useAuth', () => ({
  useAuth: () => ({ role: mocks.role }),
}));

import { PricePostingReviewPanel } from './PricePostingReviewPanel';

const program = {
  id: 'synthetic-rule-id',
  program_key: 'NJ_ECPL',
  version: 1,
  country_code: 'US',
  region_code: 'NJ',
  program_name: 'Synthetic New Jersey Current Price List',
  authority_title: 'Synthetic NJ ABC',
  authority_url: 'https://www.nj.gov/',
  applicability_note: 'Review exact scope and current format',
  official_template_url: null,
  filing_deadline_rule: null,
  human_verified_for_tenant: false,
  submission_permitted: false,
};

let host: HTMLDivElement | undefined;
let root: Root | undefined;

async function flush() {
  for (let n = 0; n < 4; n += 1) {
    await act(async () => { await Promise.resolve(); });
  }
}

async function mount() {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => { root?.render(<PricePostingReviewPanel />); });
  await flush();
  return host;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.role = 'owner';
  mocks.listPrograms.mockResolvedValue({
    programs: [program], count: 1, agency_submission_enabled: false,
    notice: 'Candidate only',
  });
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
});

describe('human price-posting source review', () => {
  it('labels unverified deadlines and offers review, never a filing action', async () => {
    const page = await mount();
    expect(page.textContent).toContain('Filing deadline not verified');
    expect(page.textContent).toContain('Official template not verified');
    expect(page.textContent).toContain('Agency submissions are disabled');
    expect(page.querySelector('a')?.getAttribute('href')).toBe('https://www.nj.gov/');
    expect(page.textContent).toContain('Review source and applicability');
    expect(page.querySelector('button[title="Submit to regulator"]')).toBeNull();
    expect(mocks.recordHumanReview).not.toHaveBeenCalled();
  });

  it('keeps pricing-only staff read-only', async () => {
    mocks.role = 'pricing_billbacks';
    const page = await mount();
    expect(page.textContent).toContain('Read-only access');
    expect(page.textContent).not.toContain('Review source and applicability');
    expect(mocks.recordHumanReview).not.toHaveBeenCalled();
  });

  it('does not show a review button when evidence is already recorded', async () => {
    mocks.listPrograms.mockResolvedValue({
      programs: [{ ...program, human_verified_for_tenant: true }],
      count: 1, agency_submission_enabled: false,
    });
    const page = await mount();
    expect(page.textContent).toContain('Documentary review recorded');
    expect(page.textContent).not.toContain('Review source and applicability');
  });

  it('treats unavailable source as an error, never clearance', async () => {
    mocks.listPrograms.mockRejectedValue(new Error('Synthetic rulebook source outage'));
    const page = await mount();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Synthetic rulebook source outage');
    expect(page.textContent).not.toContain('No rule versions are available');
    expect(mocks.recordHumanReview).not.toHaveBeenCalled();
  });

  it('does not submit an attestation with unchecked evidence', async () => {
    const page = await mount();
    const button = Array.from(page.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('Review source and applicability'));
    await act(async () => { button?.click(); });
    expect(page.querySelectorAll('input[type="checkbox"]').length).toBe(3);
    const save = Array.from(page.querySelectorAll('button'))
      .find((b) => b.textContent?.includes('Record human source review'));
    expect(save?.disabled).toBe(true);
    expect(mocks.recordHumanReview).not.toHaveBeenCalled();
  });
});
