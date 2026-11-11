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

// Mirrors backend/app/auth/rbac.py ROLE_PERMISSIONS exactly. There is no
// wildcard: a role has a permission only if it is listed. (The server enforces
// all of this; the copy here only decides which buttons to show.)
const DATA_PERMISSIONS = [
  'view_all', 'view_dashboard', 'view_financials', 'view_inventory', 'view_orders',
  'view_customers', 'view_own_accounts', 'run_reports', 'manage_inventory',
  'approve_actions', 'trigger_actions',
];

const ROLE_PERMISSIONS: Record<Role, Set<string>> = {
  owner: new Set([...DATA_PERMISSIONS, 'manage_users', 'manage_billing', 'manage_integrations']),
  manager: new Set(DATA_PERMISSIONS),
  sales_rep: new Set(['view_own_accounts', 'view_inventory', 'view_dashboard']),
  back_office: new Set(['view_inventory', 'view_orders', 'run_reports', 'view_customers', 'view_dashboard']),
  viewer: new Set(['view_dashboard']),
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
    return ROLE_PERMISSIONS[role].has(permission);
  }

  return {
    role,
    hasPermission,
    // Convenience booleans used across components
    canApprove:         hasPermission('approve_actions'),
    canViewFinancials:  hasPermission('view_financials'),
    canDraftPO:         hasPermission('trigger_actions'),
    canTriggerActions:  hasPermission('trigger_actions'),
    canAccessChat:      role !== 'viewer',
    canAccessSettings:  hasPermission('manage_billing') || role === 'manager',
    canManageUsers:        hasPermission('manage_users'),
    canManageIntegrations: hasPermission('manage_integrations'),
    canManageBilling:      hasPermission('manage_billing'),
    canAccessAccounts:  hasPermission('view_all') || hasPermission('view_own_accounts') || hasPermission('view_customers'),
    canAccessInventory: hasPermission('view_inventory') || hasPermission('view_all'),
  };
}
