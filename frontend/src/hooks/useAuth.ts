/**
 * useAuth — central role/permission hook.
 *
 * Reads the role from the JWT payload (not localStorage) so it cannot be
 * tampered with via DevTools. The JWT is signed by the backend; modifying
 * the payload would invalidate the signature and all API calls would 403.
 *
 * Exposes:
 *   · role           — the raw role string, sourced from JWT
 *   · hasPermission  — checks the ROLE_PERMISSIONS map (mirrors backend rbac.py)
 *   · helper booleans for the most common UI gates
 */

export type Role = 'owner' | 'manager' | 'sales_rep' | 'back_office' | 'viewer';

// Mirrors backend/app/auth/rbac.py ROLE_PERMISSIONS exactly
const ROLE_PERMISSIONS: Record<Role, Set<string>> = {
  owner: new Set([
    'view_all', 'manage_users', 'approve_actions', 'manage_billing',
    'view_financials', 'trigger_actions',
  ]),
  manager: new Set([
    'view_all', 'approve_actions', 'trigger_actions', 'view_financials',
  ]),
  sales_rep: new Set([
    'view_own_accounts', 'view_inventory',
  ]),
  back_office: new Set([
    'view_inventory', 'view_orders', 'run_reports', 'view_customers',
  ]),
  viewer: new Set([
    'view_dashboard',
  ]),
};

/**
 * Decode the JWT payload section (base64url → JSON) and return the role claim.
 * No crypto needed on the client — the backend verifies the signature on every
 * API call. We only read the claim for UI gating.
 */
function parseJwtRole(token: string): string {
  try {
    const base64url = token.split('.')[1];
    // base64url → base64: replace - with + and _ with /
    const base64 = base64url.replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(base64));
    return typeof payload.role === 'string' ? payload.role : 'viewer';
  } catch {
    return 'viewer';
  }
}

export function useAuth() {
  // Source of truth: the signed JWT token — not the writable localStorage.secretary_role
  const token = localStorage.getItem('secretary_token') ?? '';
  const raw   = token ? parseJwtRole(token) : 'viewer';
  const role  = (Object.keys(ROLE_PERMISSIONS).includes(raw) ? raw : 'viewer') as Role;

  function hasPermission(permission: string): boolean {
    const perms = ROLE_PERMISSIONS[role];
    // view_all is a super-permission (owner + manager bypass all checks)
    return perms.has(permission) || perms.has('view_all');
  }

  return {
    role,
    hasPermission,
    // Convenience booleans used across components
    canApprove:         hasPermission('approve_actions'),
    canViewFinancials:  hasPermission('view_financials'),
    canDraftPO:         hasPermission('view_inventory') && hasPermission('trigger_actions')
                          || role === 'back_office',
    canTriggerActions:  hasPermission('trigger_actions'),
    canAccessChat:      role !== 'viewer',
    canAccessSettings:  hasPermission('manage_billing') || role === 'manager',
    canAccessAccounts:  hasPermission('view_all') || hasPermission('view_own_accounts') || hasPermission('view_customers'),
    canAccessInventory: hasPermission('view_inventory') || hasPermission('view_all'),
  };
}
