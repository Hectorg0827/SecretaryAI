/**
 * Role-aware presentation rules for Lola.
 *
 * IMPORTANT: this is UI gating only (defence in depth). Workstream 1's
 * backend is the authority for what a user may see or do. If the server
 * sends something this file would hide, we hide it and log nothing sensitive.
 */
import { Role, roleHasPermission } from '../hooks/useAuth';
import type { Briefing, BriefingSectionId, EvidenceCard, ActionPreview } from './contract';

/** Viewers have no conversational access (matches backend chat permission). */
export function canUseLola(role: Role): boolean {
  return role !== 'viewer';
}

export function canSeeFinancials(role: Role): boolean {
  return roleHasPermission(role, 'view_financials');
}

export function canApproveActions(role: Role): boolean {
  return roleHasPermission(role, 'approve_actions');
}

/** Which command-center sections each role may see. */
export const SECTIONS_BY_ROLE: Record<Role, BriefingSectionId[]> = {
  owner: [
    'morning_briefing', 'critical_alerts', 'performance', 'department_priorities',
    'logistics_exceptions', 'financial_risks', 'pending_approvals', 'next_actions', 'recent_tasks',
  ],
  manager: [
    'morning_briefing', 'critical_alerts', 'performance', 'department_priorities',
    'logistics_exceptions', 'financial_risks', 'pending_approvals', 'next_actions', 'recent_tasks',
  ],
  sales_rep: ['morning_briefing', 'department_priorities', 'next_actions', 'recent_tasks'],
  back_office: ['morning_briefing', 'department_priorities', 'logistics_exceptions', 'next_actions', 'recent_tasks'],
  viewer: [],
};

function isSensitive(s?: string): boolean {
  return s === 'financial' || s === 'executive';
}

/** Remove sections (and sensitive sections) the role may not see. */
export function filterBriefingForRole(b: Briefing, role: Role): Briefing {
  const allowed = new Set(SECTIONS_BY_ROLE[role]);
  const fin = canSeeFinancials(role);
  return {
    ...b,
    sections: b.sections.filter((s) => allowed.has(s.id) && (fin || !isSensitive(s.sensitivity))),
  };
}

/**
 * Returns the card unchanged, or a `forbidden` placeholder with no data if the
 * role may not see it. The placeholder intentionally carries NO values.
 */
export function guardCard(card: EvidenceCard, role: Role): EvidenceCard {
  if (!isSensitive(card.sensitivity) || canSeeFinancials(role)) return card;
  return {
    id: card.id,
    kind: 'metrics',
    title: card.title,
    availability: 'forbidden',
    availability_note: 'This information is restricted for your role.',
    metrics: [],
  };
}

/**
 * The server decides who may approve (policies can be finer than roles, e.g.
 * a rep sending their own customer email, or back office opening a vendor
 * claim). The client only refuses to show approval controls to roles that
 * can never act (viewers). The server re-checks on every approval.
 */
export function guardAction(action: ActionPreview, role: Role): ActionPreview {
  if (action.can_approve && !canUseLola(role)) {
    return { ...action, can_approve: false, approval_note: action.approval_note ?? 'Your role can’t approve actions.' };
  }
  return action;
}
