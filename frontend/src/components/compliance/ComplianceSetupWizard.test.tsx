// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import { ComplianceSetupWizard } from './ComplianceSetupWizard';

describe('Compliance setup exit path', () => {
  it('has an accessible close button and supports Escape without saving', async () => {
    const onDismiss = vi.fn();
    const rootNode = document.createElement('div');
    document.body.appendChild(rootNode);
    const root = createRoot(rootNode);

    try {
      await act(async () => {
        root.render(<ComplianceSetupWizard onComplete={vi.fn()} onDismiss={onDismiss} />);
      });
      const modal = rootNode.querySelector('[role="dialog"][aria-modal="true"]');
      expect(modal?.getAttribute('aria-labelledby')).toBe('compliance-setup-title');
      const closeButton = rootNode.querySelector<HTMLButtonElement>(
        'button[aria-label="Close compliance setup"]',
      );
      expect(closeButton).not.toBeNull();
      expect(closeButton?.disabled).toBe(false);
      await act(async () => closeButton?.click());
      expect(onDismiss).toHaveBeenCalledTimes(1);

      await act(async () => {
        window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      });
      expect(onDismiss).toHaveBeenCalledTimes(2);
    } finally {
      await act(async () => root.unmount());
      rootNode.remove();
    }
  });
});
