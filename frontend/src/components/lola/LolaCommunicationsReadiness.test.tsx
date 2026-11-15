import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../../lib/api', () => ({ api: { get: mock.get } }));
import { LolaCommunicationsReadiness } from './LolaCommunicationsReadiness';

const readiness = {
  source: 'existing_employee_private_tables',
  email_state: 'single_connection_recorded',
  whatsapp_state: 'existing_consent_and_ownership_metadata_present',
  contact_state: 'not_checked',
  provider_message_coverage_verified: false,
  cross_channel_work_item_verified: false,
  eligible_for_follow_up_engine: false,
  proactive_notifications_enabled_by_this_check: false,
  external_writes_executed: false,
};
let root: Root | null = null;
let mount: HTMLDivElement | null = null;
async function render(enabled = true) {
  mount = document.createElement('div');
  document.body.appendChild(mount);
  root = createRoot(mount);
  await act(async () => {
    root?.render(<MemoryRouter><LolaCommunicationsReadiness enabled={enabled} /></MemoryRouter>);
  });
  return mount;
}
beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  mock.get.mockResolvedValue(readiness);
});
afterEach(async () => {
  if (root) await act(async () => { root?.unmount(); });
  mount?.remove(); mount = null; root = null;
});
describe('Lola private communication readiness', () => {
  it('does not load restricted roles or show any private data', async () => {
    const node = await render(false);
    expect(node.textContent).toBe('');
    expect(mock.get).not.toHaveBeenCalled();
  });
  it('shows conservative connection metadata not approved follow-up', async () => {
    const node = await render();
    expect(mock.get).toHaveBeenCalledWith('/api/lola/chief-of-staff/continuity/readiness');
    expect(node.textContent).toContain('One employee email connection recorded');
    expect(node.textContent).toContain('WhatsApp consent and ownership metadata recorded');
    expect(node.textContent).toContain('Not established');
    expect(node.textContent).toContain('not proof of complete message history');
    expect(node.textContent).not.toContain('Send reminder');
    expect(node.querySelector('button')).toBeNull();
  });
  it('rejects fabricated API claims of complete provider sync', async () => {
    mock.get.mockResolvedValue({ ...readiness, provider_message_coverage_verified: true });
    const node = await render();
    expect(node.textContent).toContain('Connection readiness unavailable');
    expect(node.textContent).not.toContain('One employee email connection recorded');
  });
  it('treats source outage as unavailable rather than disconnected', async () => {
    mock.get.mockRejectedValue(new Error('private provider exception'));
    const node = await render();
    expect(node.textContent).toContain('Connection readiness unavailable');
    expect(node.textContent).not.toContain('private provider exception');
    expect(node.textContent).not.toContain('No employee WhatsApp connection recorded');
  });
  it('renders pending consent without suggesting an action is authorized', async () => {
    mock.get.mockResolvedValue({
      ...readiness, email_state: 'no_active_email_connection_recorded',
      whatsapp_state: 'connection_pending_or_consent_incomplete',
    });
    const node = await render();
    expect(node.textContent).toContain('WhatsApp connection or consent incomplete');
    expect(node.textContent).toContain('No connected employee mailbox verified');
    expect(node.textContent).toContain('No communication actions are enabled');
  });
});
