import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WeeklyReportResponseV1 } from '../../lib/api';

const mocks = vi.hoisted(() => ({ weekly: vi.fn() }));

vi.mock('../../lib/api', () => ({ api: { briefing: { weekly: mocks.weekly } } }));

import { WeeklyReport } from './WeeklyReport';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

function fixture(): WeeklyReportResponseV1 {
  return {
    report: {
      status: 'ready',
      report_text: 'Sales rose 12% to $1,500.\n- Reorder Rye 750',
      period_start: '2026-10-25',
      period_end: '2026-10-31',
      language: 'English',
      source_quality: 'partial',
      generated_at: '2026-11-01T06:02:00Z',
      figures: {
        sales_this_week: 1500, invoices_this_week: 2, sales_prior_week: 800, invoices_prior_week: 1,
        sales_30d: 9000, accounts_total: 10, accounts_slowing: 2, accounts_at_risk: 1,
        accounts_dormant: 1, accounts_unscored: 0, inventory_critical: 1, inventory_low: 1,
        inventory_unassessed: 3,
      },
    },
    last_run: {
      status: 'succeeded', reason: null, period_start: '2026-10-25', period_end: '2026-10-31',
      attempted_at: '2026-11-01T06:02:00Z',
    },
  };
}

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(<WeeklyReport />); });
  await act(async () => { await Promise.resolve(); });
  return container;
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mocks.weekly.mockResolvedValue(fixture());
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('weekly report card', () => {
  it('shows the stored report, its week and the figures it was written from', async () => {
    const node = await render();
    const text = node.textContent ?? '';
    expect(mocks.weekly).toHaveBeenCalledTimes(1);
    expect(text).toContain('Sales rose 12% to $1,500.');
    expect(text).toContain('Oct 25, 2026');
    expect(text).toContain('Oct 31, 2026');
    expect(text).toContain('$1,500.00 in sales this week (2 invoices)');
    expect(text).toContain('1 at-risk and 1 dormant of 10 accounts');
    expect(text).toContain('Written by AI');
    expect(node.querySelector('[role="alert"]')).toBeNull();
  });

  it('shows a failed week plainly and never invents a report', async () => {
    mocks.weekly.mockResolvedValue({
      report: null,
      last_run: {
        status: 'failed', reason: 'source_unavailable', period_start: '2026-10-25',
        period_end: '2026-10-31', attempted_at: '2026-11-01T06:02:00Z',
      },
    });
    const node = await render();
    const alert = node.querySelector('[role="alert"]')?.textContent ?? '';
    expect(alert).toContain('could not be prepared because your business data could not be read');
    expect(node.querySelector('article')).toBeNull();
  });

  it('keeps the last good report on screen under a newer failed week', async () => {
    const data = fixture();
    data.last_run = {
      status: 'failed', reason: 'generation_failed', period_start: '2026-11-01',
      period_end: '2026-11-07', attempted_at: '2026-11-08T06:02:00Z',
    };
    mocks.weekly.mockResolvedValue(data);
    const node = await render();
    expect(node.querySelector('[role="alert"]')?.textContent).toContain('Below is your most recent report.');
    expect(node.textContent).toContain('Sales rose 12% to $1,500.');
  });

  it('shows a friendly empty state before the first report', async () => {
    mocks.weekly.mockResolvedValue({ report: null, last_run: null });
    const node = await render();
    expect(node.textContent).toContain('Your first weekly report will appear here');
  });

  it.each([
    ['request failure', () => mocks.weekly.mockRejectedValue(new Error('503'))],
    ['unexpected field', () => mocks.weekly.mockResolvedValue({ ...fixture(), extra: true })],
    ['non-finite sales', () => {
      const data = fixture();
      if (data.report) data.report.figures.sales_this_week = Number.NaN;
      mocks.weekly.mockResolvedValue(data);
    }],
    ['empty report text', () => {
      const data = fixture();
      if (data.report) data.report.report_text = '  ';
      mocks.weekly.mockResolvedValue(data);
    }],
  ])('treats %s as unavailable, not as a report', async (_label, arrange) => {
    arrange();
    const node = await render();
    expect(node.querySelector('[role="alert"]')?.textContent).toContain('couldn’t load your weekly report');
    expect(node.querySelector('article')).toBeNull();
  });
});
