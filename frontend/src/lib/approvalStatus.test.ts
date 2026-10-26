import { describe, expect, it } from 'vitest';
import { approvalNotice } from './approvalStatus';

describe('approval result wording', () => {
  it('does not equate queued authorization with completed delivery', () => {
    const notice = approvalNotice({ draft_id: 'synthetic', status: 'approved', execution_status: 'queued' });
    expect(notice.severity).toBe('warning');
    expect(notice.message).toContain('not confirmed');
  });

  it('recognizes a provider acknowledgment only when explicitly reported', () => {
    expect(approvalNotice({ draft_id: 'synthetic', status: 'approved', execution_status: 'sent' }).severity).toBe('success');
  });

  it('prevents an already-processed result from being described as a fresh send', () => {
    const notice = approvalNotice({ draft_id: 'synthetic', status: 'approved', execution_status: 'sent', already_processed: true });
    expect(notice.severity).toBe('warning');
    expect(notice.message).toContain('already resolved');
  });

  it('treats missing or uncertain execution evidence as unverified', () => {
    expect(approvalNotice({ draft_id: 'synthetic', status: 'approved' }).severity).toBe('warning');
    expect(approvalNotice({ draft_id: 'synthetic', status: 'approved', execution_status: 'needs_reconciliation' }).message).toContain('not confirmed');
  });
});
