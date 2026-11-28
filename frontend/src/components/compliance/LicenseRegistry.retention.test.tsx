// @vitest-environment happy-dom
import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import { LicenseRegistry } from './LicenseRegistry';

describe('Compliance registry retention controls', () => {
  it('shows retained records without a delete action across every registry tab', async () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    const root = createRoot(host);
    try {
      await act(async () => {
        root.render(
          <LicenseRegistry
            licenses={[{
              id: 'license-one', state_code: 'NY', license_type: 'importer',
              license_number: 'SYNTHETIC', expiration_date: '2030-01-01',
              status: 'active', annual_fee: 0,
            }]}
            brandRegistrations={[{
              id: 'brand-one', product_id: 'synthetic-one', state_code: 'NY',
              registration_number: 'SYNTHETIC', expiration_date: '2030-01-01',
              status: 'active',
            }]}
            federalPermits={[{
              id: 'permit-one', permit_type: 'importer', permit_number: 'SYNTHETIC',
              expiration_date: '2030-01-01', status: 'active',
            }]}
            loading={false}
            onRefresh={vi.fn()}
          />,
        );
      });

      expect(host.querySelector('[role="note"]')?.textContent).toContain('Deletion is disabled');
      for (const tabName of ['State Licenses', 'Brand Registrations', 'Federal Permits']) {
        const tab = Array.from(host.querySelectorAll('button')).find(
          (b) => b.textContent?.includes(tabName),
        );
        expect(tab).toBeDefined();
        await act(async () => tab?.click());
        expect(host.textContent).toContain('SYNTHETIC');
        expect(host.querySelector('button[title="Delete"]')).toBeNull();
      }
    } finally {
      await act(async () => root.unmount());
      host.remove();
    }
  });
});
