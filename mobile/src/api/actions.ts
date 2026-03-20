import { apiFetch } from './client';

export interface Draft {
  id: string;
  action_type: string;
  content: Record<string, unknown>;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  created_by: string;
}

export async function getPendingActions(): Promise<{ drafts: Draft[] }> {
  return apiFetch<{ drafts: Draft[] }>('/api/actions/pending');
}

export async function approveAction(
  draftId: string,
  editedContent?: Record<string, unknown>,
): Promise<void> {
  await apiFetch(`/api/actions/approve/${draftId}`, {
    method: 'POST',
    body: JSON.stringify(editedContent ?? {}),
  });
}

export async function rejectAction(draftId: string, reason?: string): Promise<void> {
  await apiFetch(`/api/actions/reject/${draftId}`, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  });
}
