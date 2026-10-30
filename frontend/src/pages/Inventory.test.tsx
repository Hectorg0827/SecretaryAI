import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../lib/api';
import { Inventory, INVENTORY_PAGE_SIZE } from './Inventory';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

async function flush() {
  for (let i = 0; i < 10; i += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
}

async function mount(node: ReactNode) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(node); });
  await flush();
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  localStorage.clear();
  vi.restoreAllMocks();
});

type Status = 'healthy' | 'low' | 'critical' | 'out_of_stock';

function product(n: number, stock_status: Status = 'healthy') {
  return {
    item_id: `QB-${n}`,
    product_name: `Synthetic Product ${n}`,
    total_qty: 10,
    weeks_remaining: 8,
    stock_status,
    needs_po: stock_status === 'critical' || stock_status === 'out_of_stock',
  };
}

/** A catalog of `total` products served in pages like GET /api/inventory. */
function catalog(total: number, statusOf: (n: number) => Status = () => 'healthy') {
  const all = Array.from({ length: total }, (_, i) => product(i + 1, statusOf(i + 1)));
  return (path: string) => {
    const match = /^\/api\/inventory\?limit=(\d+)&offset=(\d+)$/.exec(path);
    if (!match) throw new Error(`Unexpected GET ${path}`);
    const limit = Number(match[1]);
    const offset = Number(match[2]);
    return { items: all.slice(offset, offset + limit), total, offset, limit };
  };
}

function urls(get: { mock: { calls: unknown[][] } }) {
  return get.mock.calls.map((call) => call[0]);
}

function filterButton(page: ParentNode, label: string) {
  return Array.from(page.querySelectorAll('button')).find((b) => b.textContent?.startsWith(label));
}

describe('inventory page loads every product', () => {
  it('pages through the whole catalog and counts every product', async () => {
    const serve = catalog(450, (n) => (n > 440 ? 'critical' : 'healthy'));
    const get = vi.spyOn(api, 'get').mockImplementation(async (path: string) => serve(path));
    const page = await mount(<Inventory />);

    expect(urls(get)).toEqual([
      `/api/inventory?limit=${INVENTORY_PAGE_SIZE}&offset=0`,
      `/api/inventory?limit=${INVENTORY_PAGE_SIZE}&offset=200`,
      `/api/inventory?limit=${INVENTORY_PAGE_SIZE}&offset=400`,
    ]);
    const text = page.textContent ?? '';
    expect(text).toContain('450 products');
    expect(text).toContain('10 critical');
    expect(filterButton(page, 'All')?.textContent).toBe('All (450)');
    expect(filterButton(page, 'Critical')?.textContent).toBe('Critical (10)');
    expect(page.querySelectorAll('tbody tr')).toHaveLength(450);
    // A product beyond the first 50 (the old single-page limit) is listed.
    expect(text).toContain('Synthetic Product 450');
  });

  it('makes a single request when everything fits on one page', async () => {
    const get = vi.spyOn(api, 'get').mockImplementation(async (path: string) => catalog(3)(path));
    const page = await mount(<Inventory />);
    expect(urls(get)).toEqual([`/api/inventory?limit=${INVENTORY_PAGE_SIZE}&offset=0`]);
    expect(page.textContent).toContain('3 products');
  });

  it('shows an empty catalog as no products, not as an error', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => catalog(0)(path));
    const page = await mount(<Inventory />);
    expect(page.textContent).toContain('0 products');
    expect(page.textContent).toContain('No inventory items found');
    expect(page.querySelector('[role="alert"]')).toBeNull();
  });

  it('fails the whole list when a later page fails, then retries', async () => {
    const serve = catalog(250);
    let failSecondPage = true;
    const get = vi.spyOn(api, 'get').mockImplementation(async (path: string) => {
      if (path.endsWith('offset=200') && failSecondPage) {
        failSecondPage = false;
        throw new Error('Inventory source unavailable');
      }
      return serve(path);
    });
    const page = await mount(<Inventory />);

    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Inventory could not be loaded: Inventory source unavailable');
    expect(page.querySelector('table')).toBeNull();
    expect(page.textContent).not.toContain('200 products');
    expect(page.textContent).toContain('Product count not available yet');

    const retry = Array.from(page.querySelectorAll('button')).find((b) => b.textContent === 'Try again');
    await act(async () => { retry?.click(); });
    await flush();
    expect(get).toHaveBeenCalledTimes(4);
    expect(page.querySelector('[role="alert"]')).toBeNull();
    expect(page.textContent).toContain('250 products');
  });

  it('refuses a list that changed between pages', async () => {
    const first = catalog(300);
    const grown = catalog(301);
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => (
      path.endsWith('offset=0') ? first(path) : grown(path)
    ));
    const page = await mount(<Inventory />);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Inventory changed while it was loading');
    expect(page.querySelector('table')).toBeNull();
  });

  it('refuses a page that comes back empty before the total is reached', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => (
      path.endsWith('offset=0') ? catalog(300)(path) : { items: [], total: 300, offset: 200, limit: 200 }
    ));
    const page = await mount(<Inventory />);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Inventory changed while it was loading');
  });

  it('refuses a repeated product across pages', async () => {
    vi.spyOn(api, 'get').mockImplementation(async (path: string) => (
      path.endsWith('offset=0')
        ? { items: Array.from({ length: 200 }, (_, i) => product(i + 1)), total: 201 }
        : { items: [product(1)], total: 201 }
    ));
    const page = await mount(<Inventory />);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('Inventory changed while it was loading');
  });

  it('treats a response without a total as incomplete, not as the whole list', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ items: [product(1)] });
    const page = await mount(<Inventory />);
    expect(page.querySelector('[role="alert"]')?.textContent).toContain('The inventory response was incomplete.');
    expect(page.textContent).not.toContain('1 products');
  });
});
