import type { ActionApprovalResult } from './api';

export type ApprovalNotice = { severity: 'success' | 'warning'; message: string };

/** Human authorization and downstream execution must never be conflated. */
export function approvalNotice(result: ActionApprovalResult): ApprovalNotice {
  if (result.already_processed) {
    return { severity: 'warning', message: 'This draft was already resolved. Check its audit record before retrying.' };
  }
  switch (result.execution_status) {
    case 'sent':
      return { severity: 'success', message: 'Approved; the provider acknowledged delivery.' };
    case 'queued':
    case 'dispatched':
      return { severity: 'warning', message: 'Approved; execution is queued, not confirmed complete.' };
    case 'failed':
    case 'needs_reconciliation':
    case 'unsupported':
      return { severity: 'warning', message: 'Approval recorded, but execution was not confirmed. Review the audit trail.' };
    default:
      return { severity: 'warning', message: 'Approval recorded; delivery status is not verified.' };
  }
}
