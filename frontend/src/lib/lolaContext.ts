/** Non-sensitive navigation context only. The backend enforces access independently. */
export type LolaWorkspace = 'home' | 'dashboard' | 'finance' | 'accounts' | 'inventory'
  | 'inbox' | 'work' | 'logistics' | 'compliance' | 'insights' | 'connectors'
  | 'settings' | 'account-security' | 'audit-log' | 'admin';
export type LolaEntityType = 'account' | 'item' | 'shipment' | 'order' | 'product' | 'alert';
export interface LolaContext {
  workspace: LolaWorkspace;
  entity_type?: LolaEntityType;
  entity_label?: string;
}

export function workspaceFromPath(pathname: string): LolaWorkspace | null {
  const name = pathname.split('/').filter(Boolean)[0] ?? 'home';
  const allowed: LolaWorkspace[] = ['home', 'dashboard', 'finance', 'accounts', 'inventory', 'inbox',
    'work', 'logistics', 'compliance', 'insights', 'connectors',
    'settings', 'account-security', 'audit-log', 'admin'];
  if (pathname === '/') return 'home';
  return allowed.includes(name as LolaWorkspace) ? name as LolaWorkspace : null;
}

export function contextKey(context: LolaContext | null): string {
  if (!context) return 'none';
  return [context.workspace, context.entity_type ?? '', context.entity_label ?? ''].join('|');
}

export function cleanContext(context: LolaContext): LolaContext {
  return {
    workspace: context.workspace,
    ...(context.entity_type ? { entity_type: context.entity_type } : {}),
    ...(context.entity_label ? { entity_label: context.entity_label.trim().slice(0, 100) } : {}),
  };
}

/** Restore only validated navigation metadata from a persisted user message. */
export function readContext(input: unknown): LolaContext | null {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return null;
  const value = input as Record<string, unknown>;
  if (typeof value.workspace !== 'string') return null;
  const ws = workspaceFromPath('/' + value.workspace);
  if (!ws || (typeof value.entity_type === 'string' &&
     !['account', 'item', 'shipment', 'order', 'product', 'alert'].includes(value.entity_type))) return null;
  return cleanContext({
    workspace: ws,
    ...(typeof value.entity_type === 'string' ? { entity_type: value.entity_type as LolaEntityType } : {}),
    ...(typeof value.entity_label === 'string' ? { entity_label: value.entity_label } : {}),
  });
}
