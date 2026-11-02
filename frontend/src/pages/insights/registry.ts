/**
 * Insight pages register themselves: every file in ./modules (except tests)
 * exports `meta` and a default component, and appears under /insights/<slug>
 * without editing App.tsx or the sidebar.
 */
import type { ComponentType } from 'react';
import type { Role } from '../../hooks/useAuth';

export type InsightGroup =
  | 'Customers' | 'Finance' | 'Growth' | 'Pricing'
  | 'Supply chain' | 'Operations' | 'People' | 'Governance';

export interface InsightMeta {
  slug: string;
  title: string;
  description: string;
  group: InsightGroup;
  roles: readonly Role[];
}

export interface InsightModule {
  meta: InsightMeta;
  default: ComponentType;
}

const loaded = import.meta.glob<InsightModule>(
  ['./modules/*.tsx', '!./modules/*.test.tsx'],
  { eager: true },
);

export const INSIGHTS: InsightModule[] = Object.values(loaded)
  .filter((mod) => mod?.meta?.slug && typeof mod.default === 'function')
  .sort((a, b) => a.meta.group.localeCompare(b.meta.group) || a.meta.title.localeCompare(b.meta.title));

export function insightsForRole(role: Role): InsightModule[] {
  return INSIGHTS.filter((mod) => mod.meta.roles.includes(role));
}

export function findInsight(slug: string, role: Role): InsightModule | undefined {
  return insightsForRole(role).find((mod) => mod.meta.slug === slug);
}
