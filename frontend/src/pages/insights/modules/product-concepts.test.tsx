import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { api } from '../../../lib/api';
import ProductConcepts, { meta } from './product-concepts';

vi.mock('../../../lib/api', () => ({ api: { get: vi.fn(), post: vi.fn() } }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const response = {
  status: 'analysis_only', source: 'user_supplied', requires_human_review: true,
  actions_executed: false, generated_at: '2026-10-28T00:00:00Z',
  concept_name: 'Sparkling rose 4-pack',
  addressable_whitespace_cases: 2000, projected_new_product_cases: 200,
  cannibalized_incumbent_cases: 50, gross_new_product_contribution: 2000,
  incumbent_contribution_lost: 300, incremental_first_year_profit: 700,
  estimated_payback_months: 7.1, documented_assumptions: true,
  risk_flags: ['cannibalization_requires_portfolio_review'],
};

const inputs: Record<string, string> = {
  concept_name: 'Sparkling rose 4-pack',
  observed_category_demand_cases: '10000', existing_served_category_cases: '8000',
  proposed_capture_pct: '10', wholesale_price_per_case: '30', landed_cost_per_case: '18',
  variable_launch_cost_per_case: '2', one_time_launch_cost: '1000', cannibalization_pct: '25',
  incumbent_contribution_per_case: '6', demand_source_reference: 'Category study', assumption_version: 'v1',
};

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function render() {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(<MemoryRouter><ProductConcepts /></MemoryRouter>);
  });
  return container;
}

async function fill(page: HTMLElement, values: Record<string, string>) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
  await act(async () => {
    for (const [name, value] of Object.entries(values)) {
      const input = page.querySelector(`input[name="${name}"]`);
      setter.call(input, value);
      input!.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });
}

async function submit(page: HTMLElement) {
  await act(async () => {
    page.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  });
}

beforeEach(() => {
  vi.mocked(api.post).mockReset();
});

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
});

describe('new product ideas page', () => {
  it('is offered only to roles with financial access', () => {
    expect([...meta.roles].sort()).toEqual(['manager', 'owner']);
  });

  it('submits the entered assumptions and shows the assessment', async () => {
    vi.mocked(api.post).mockResolvedValue(response);
    const page = await render();
    await fill(page, inputs);
    await submit(page);
    expect(api.post).toHaveBeenCalledWith('/api/insights/product-concepts/preview', {
      concept_name: 'Sparkling rose 4-pack', demand_source_reference: 'Category study', assumption_version: 'v1',
      observed_category_demand_cases: 10000, existing_served_category_cases: 8000, proposed_capture_pct: 10,
      wholesale_price_per_case: 30, landed_cost_per_case: 18, variable_launch_cost_per_case: 2,
      one_time_launch_cost: 1000, cannibalization_pct: 25, incumbent_contribution_per_case: 6,
    });
    const text = page.textContent ?? '';
    expect(text).toContain('2,000 cases');
    expect(text).toContain('$700.00');
    expect(text).toContain('7.1');
    expect(text).toContain('It takes sales from your current products');
  });

  it('does not submit with numbers missing', async () => {
    const page = await render();
    await fill(page, { concept_name: 'Idea' });
    await submit(page);
    expect(api.post).not.toHaveBeenCalled();
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Fill in every number');
  });

  it('shows the server validation message on failure', async () => {
    vi.mocked(api.post).mockRejectedValue(new Error('Served category volume exceeds total observed demand'));
    const page = await render();
    await fill(page, { ...inputs, existing_served_category_cases: '20000' });
    await submit(page);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('exceeds total observed demand');
    expect(page.querySelector('section[aria-label="Assessment"]')).toBeNull();
  });
});
