import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AccountHealthPanel } from './AccountHealthPanel';

describe('dashboard health classifications', () => {
  it('preserves the full account total and does not classify unknown as at risk', async () => {
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      await act(async () => {
        root.render(
          <MemoryRouter>
            <AccountHealthPanel summary={{
              healthy: 16, slowing: 3, at_risk: 0, dormant: 0, unknown: 10,
            }} />
          </MemoryRouter>,
        );
      });
      expect(container.textContent).toContain('Account Health29');
      expect(container.textContent).toContain('Unknown10');
      expect(container.textContent).toContain('At Risk0');
      expect(container.textContent).not.toContain('At Risk10');
    } finally {
      await act(async () => root.unmount());
      container.remove();
    }
  });
});
