import { apiFetch } from './client';

export async function registerPushToken(token: string, platform: 'ios' | 'android' | 'web'): Promise<void> {
  await apiFetch('/api/notifications/register', {
    method: 'POST',
    body: JSON.stringify({ token, platform }),
  });
}

export async function unregisterPushToken(token: string): Promise<void> {
  await apiFetch(`/api/notifications/unregister?token=${encodeURIComponent(token)}`, {
    method: 'DELETE',
  });
}
