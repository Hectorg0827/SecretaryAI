import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { InventoryPanel } from './InventoryPanel';

async function renderPanel(unassessedCount: number | null) {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  await act(async () => root.render(
    <MemoryRouter>
      <InventoryPanel alerts={[]} unassessedCount={unassessedCount} />
    </MemoryRouter>,
  ));
  return { container, root };
}

describe('dashboard inventory coverage (synthetic data)', () => {
  it('shows products without sell rates instead of an all-clear', async () => {
    const { container, root } = await renderPanel(4);
    try {
      expect(container.textContent).toContain('No low-stock alerts reported');
      expect(container.textContent).toContain('4 products not assessed');
      expect(container.textContent).not.toContain('All products at healthy levels');
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  it('does not invent an assessment count when coverage is absent', async () => {
    const { container, root } = await renderPanel(null);
    try {
      expect(container.textContent).toContain('Inventory assessment coverage unavailable');
      expect(container.textContent).not.toContain('All products at healthy levels');
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });

  it('shows no unassessed warning when the backend confirms full coverage', async () => {
    const { container, root } = await renderPanel(0);
    try {
      expect(container.textContent).toContain('No low-stock alerts reported');
      expect(container.textContent).not.toContain('not assessed');
      expect(container.textContent).not.toContain('coverage unavailable');
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
});
