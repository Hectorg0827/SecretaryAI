import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }));
vi.mock('../lib/api', () => ({ api: mocks }));

import { useInboxStore, type InboxItem } from './inboxStore';

const verifiedItem: InboxItem = {
  id: 'fixture-message', type: 'email', priority: 'high', title: 'Fixture email',
  subtitle: 'Synthetic sender', timestamp: '2026-10-26T12:00:00Z',
  is_read: false, payload: {},
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(console, 'error').mockImplementation(() => {});
  useInboxStore.setState({
    items: [], unread_count: null, loading: false, error: null,
    mutationError: null, selectedId: null, filter: 'all',
  });
});

afterEach(() => vi.restoreAllMocks());

describe('unified inbox source evidence (synthetic responses)', () => {
  it('keeps the count unknown after transport or malformed HTTP 200 failures', async () => {
    mocks.get.mockResolvedValueOnce({ items: [verifiedItem], unread_count: '0' });
    await useInboxStore.getState().fetch();
    expect(useInboxStore.getState().unread_count).toBeNull();
    expect(useInboxStore.getState().error).toMatch(/No empty-state conclusion/);

    mocks.get.mockRejectedValueOnce(new Error('Synthetic network outage'));
    await useInboxStore.getState().fetch();
    expect(useInboxStore.getState().unread_count).toBeNull();
    expect(useInboxStore.getState().items).toEqual([]);
  });

  it('accepts explicit zero but rejects malformed item records', async () => {
    mocks.get.mockResolvedValueOnce({ items: [verifiedItem], unread_count: 0 });
    await useInboxStore.getState().fetch();
    expect(useInboxStore.getState().unread_count).toBe(0);
    expect(useInboxStore.getState().error).toBeNull();

    mocks.get.mockResolvedValueOnce({ items: [{ ...verifiedItem, is_read: 'false' }], unread_count: 1 });
    await useInboxStore.getState().fetch();
    expect(useInboxStore.getState().unread_count).toBeNull();
    expect(useInboxStore.getState().error).not.toBeNull();
  });

  it('does not allow an older unfiltered response to replace a newer filtered result', async () => {
    let resolveFirst!: (value: unknown) => void;
    let resolveSecond!: (value: unknown) => void;
    mocks.get
      .mockReturnValueOnce(new Promise((resolve) => { resolveFirst = resolve; }))
      .mockReturnValueOnce(new Promise((resolve) => { resolveSecond = resolve; }));

    const first = useInboxStore.getState().fetch();
    useInboxStore.setState({ filter: 'email' });
    const second = useInboxStore.getState().fetch();
    resolveSecond({ items: [verifiedItem], unread_count: 1 });
    await second;
    resolveFirst({ items: [], unread_count: 0 });
    await first;

    expect(useInboxStore.getState().unread_count).toBe(1);
    expect(useInboxStore.getState().items).toHaveLength(1);
    expect(useInboxStore.getState().loading).toBe(false);
    expect(mocks.get).toHaveBeenNthCalledWith(2, '/api/inbox?filter=email');
  });

  it('does not turn an unknown count into zero after marking an item read', async () => {
    useInboxStore.setState({ items: [verifiedItem] });
    mocks.patch.mockResolvedValue({});
    await useInboxStore.getState().markRead(verifiedItem.id);
    expect(useInboxStore.getState().unread_count).toBeNull();
    expect(useInboxStore.getState().items[0].is_read).toBe(true);
  });
});
