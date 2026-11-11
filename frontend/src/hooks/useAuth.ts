/**
 * useAuth — central role/permission hook.
 *
 * The role is used only to decide which screens and buttons to show. The
 * backend enforces permissions on every request regardless of what the
 * browser believes.
 *
 * Exposes:
 *   · role           — the raw role string, sourced from JWT
 *   · hasPermission  — checks the ROLE_PERMISSIONS map (mirrors backend rbac.py)
 *   · helper booleans for the most common UI gates
 */

import { currentRole } from '../lib/session';

export type Role = 'owner' | 'manager' | 'sales_rep' | 'back_office' | 'viewer';

// Mirrors backend/app/auth/rbac.py ROLE_PERMISSIONS exactly. There is no
// wildcard: a role has a permission only if it is listed. (The server enforces
// all of this; the copy here only decides which buttons to show.)
const DATA_PERMISSIONS = [
  'view_all', 'view_dashboard', 'view_financials', 'view_inventory', 'view_orders',
  'view_customers', 'view_own_accounts', 'run_reports', 'manage_inventory',
  'approve_actions', 'trigger_actions', 'assign_accounts',
];

const ROLE_PERMISSIONS: Record<Role, Set<string>> = {
  owner: new Set([...DATA_PERMISSIONS, 'manage_users', 'manage_billing', 'manage_integrations']),
  manager: new Set(DATA_PERMISSIONS),
  sales_rep: new Set(['view_own_accounts', 'view_inventory', 'view_dashboard']),
  back_office: new Set(['view_inventory', 'view_orders', 'run_reports', 'view_customers', 'view_dashboard']),
  viewer: new Set(['view_dashboard']),
};

export function useAuth() {
  // The role comes from the session module: from the signed token in token
  // mode, or from what the server reported at sign-in in cookie mode. It only
  // decides what to SHOW — the server checks every request against the real
  // session, so changing it in the browser grants nothing.
  const raw   = currentRole();
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
    canAssignAccounts:  hasPermission('assign_accounts'),
    canAccessInventory: hasPermission('view_inventory') || hasPermission('view_all'),
  };
}
