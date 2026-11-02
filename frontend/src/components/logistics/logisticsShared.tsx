import React, { useState } from 'react';
import type { Role } from '../../hooks/useAuth';

// ── Role lists mirroring the backend checks (UI hints only; the server decides) ──
// Purchasing endpoints: require_permission(...) + _require_purchasing_employee.
export const PURCHASING_ROLES: readonly Role[] = ['owner', 'manager', 'back_office'];
// require_permission("approve_actions") and the SQL approver checks.
export const APPROVER_ROLES: readonly Role[] = ['owner', 'manager'];
// require_permission("view_orders") — warehouse previews and returns review.
export const ORDER_VIEW_ROLES: readonly Role[] = ['owner', 'manager', 'back_office'];
// require_permission("view_inventory") — customs and seasonality tools.
export const INVENTORY_VIEW_ROLES: readonly Role[] = ['owner', 'manager', 'back_office', 'sales_rep'];

export function roleAllowed(role: Role, roles: readonly Role[]): boolean {
  return roles.includes(role);
}

function tokenClaim(name: 'sub' | 'company_id'): string | null {
  try {
    const token = localStorage.getItem('secretary_token') ?? '';
    const part = token.split('.')[1];
    if (!part) return null;
    const payload = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')));
    return typeof payload[name] === 'string' ? payload[name] : null;
  } catch {
    return null;
  }
}

/** Signed-in user id from the session token. A UI hint only; the server re-checks. */
export function currentUserId(): string | null {
  return tokenClaim('sub');
}

export function currentCompanyId(): string | null {
  return tokenClaim('company_id');
}

/** Turn a server error into the server's own words plus a plain-language next step. */
export function explainError(reason: unknown, fallback: string): { message: string; hint: string } {
  const message = reason instanceof Error && reason.message ? reason.message : fallback;
  const lower = message.toLowerCase();
  let hint = '';
  if (/not permitted|denied|does not have permission|not authorized|unauthorized|http 403/.test(lower)) {
    hint = 'You are not allowed to do this step, or it must be done by a different person. Nothing was changed.';
  } else if (/conflict|already|status|http 409/.test(lower)) {
    hint = 'This does not match the record’s current status (it may have been changed by someone else, or the same reference was already used). Refresh to see the latest status.';
  } else if (/unavailable|http 5\d\d|session expired/.test(lower)) {
    hint = 'The server could not complete the request. Refresh to check whether anything changed before trying again.';
  } else if (/not found/.test(lower)) {
    hint = 'The record could not be found. Check the reference and refresh.';
  } else if (/invalid|must|required|exceed|needs|cannot|mismatch|unsupported|http 4(?:13|15|22)/.test(lower)) {
    hint = 'Please check the details you entered and try again.';
  }
  return { message, hint };
}

export function ErrorNote({ error, fallback }: { error: unknown; fallback: string }) {
  const { message, hint } = explainError(error, fallback);
  return <div role="alert" className="text-sm text-red-800 bg-red-50 border border-red-200 rounded-lg p-3">
    <p className="font-medium">{message}</p>
    {hint && <p className="mt-1">{hint}</p>}
  </div>;
}

export function ServerNote({ children }: { children: React.ReactNode }) {
  return <div role="status" className="text-sm text-emerald-900 bg-emerald-50 border border-emerald-200 rounded-lg p-3">
    {children}
  </div>;
}

export function WarningNote({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-lg p-3">{children}</p>;
}

/** Explicit confirmation step shown before any request that changes records. */
export function ConfirmPanel({ title, confirmLabel, busy, onConfirm, onCancel, children }: {
  title: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  children: React.ReactNode;
}) {
  return <div role="alertdialog" aria-label={title}
    className="border border-amber-300 bg-amber-50 rounded-xl p-4 space-y-3">
    <h4 className="font-semibold text-slate-900">{title}</h4>
    <div className="text-sm text-slate-800 space-y-2">{children}</div>
    <div className="flex flex-wrap gap-2">
      <button type="button" data-testid="confirm-write" disabled={busy} onClick={onConfirm}
        className={BUTTON_PRIMARY}>{busy ? 'Working…' : confirmLabel}</button>
      <button type="button" disabled={busy} onClick={onCancel} className={BUTTON_SECONDARY}>Cancel</button>
    </div>
  </div>;
}

/**
 * State for one confirmed write: open the confirmation, send once, keep the
 * server's answer or error. Success is only ever what the server returned.
 */
export function useConfirmedWrite<T>() {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);

  function open() {
    setResult(null);
    setError(null);
    setConfirming(true);
  }
  function cancel() {
    if (!busy) setConfirming(false);
  }
  async function send(request: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError(null);
    try {
      const data = await request();
      setResult(data);
      setConfirming(false);
      return data;
    } catch (reason) {
      setError(reason ?? new Error('Request failed'));
      setConfirming(false);
      return null;
    } finally {
      setBusy(false);
    }
  }
  return { confirming, busy, result, error, open, cancel, send };
}

export const BUTTON_PRIMARY =
  'bg-slate-800 text-white rounded-lg px-3 py-2 text-sm font-medium hover:bg-slate-700 disabled:opacity-50';
export const BUTTON_SECONDARY =
  'border border-slate-300 rounded-lg px-3 py-2 text-sm text-slate-800 bg-white hover:bg-slate-50 disabled:opacity-50';
export const INPUT =
  'w-full border border-slate-300 rounded-lg p-2 text-sm text-slate-900 bg-white';
export const CARD = 'border border-slate-200 rounded-xl p-4 space-y-3 bg-white';

export function Field({ label, htmlFor, help, children }: {
  label: string; htmlFor: string; help?: string; children: React.ReactNode;
}) {
  return <div className="space-y-1">
    <label htmlFor={htmlFor} className="block text-sm font-medium text-slate-700">{label}</label>
    {children}
    {help && <p className="text-xs text-slate-600">{help}</p>}
  </div>;
}

/** Whole, non-negative case count from a text box, or null when it is not one. */
export function parseCases(value: string, { allowZero = true, max = 10000000 } = {}): number | null {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return null;
  const parsed = Number(trimmed);
  if (!Number.isSafeInteger(parsed) || parsed > max || (!allowZero && parsed === 0)) return null;
  return parsed;
}

/** Value for a datetime-local input showing the current local time. */
export function nowForDateTimeInput(now: Date = new Date()): string {
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

/** Convert a datetime-local value (browser local time) into a timezone-aware ISO string. */
export function dateTimeInputToIso(value: string): string | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return 'Not recorded';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

export function yesNo(value: boolean | null | undefined): string {
  return value === true ? 'Yes' : value === false ? 'No' : 'Unknown';
}
