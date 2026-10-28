import type { BriefingEntryV1, BriefingSectionV1, BriefingTodayResponseV1 } from '../../lib/api';

const observed = '2026-10-26T12:00:00Z';

export function fixtureBriefing(audience_role: 'owner' | 'manager' = 'owner'): BriefingTodayResponseV1 {
  function entry(section: BriefingSectionV1, id: string, title: string): BriefingEntryV1 {
    return {
      entry_id: id, section, title, status: 'needs_review', detected_at: observed,
      proposed_action_id: null,
      evidence: [{ source_system: 'dashboard_snapshot', source_record_id: `synthetic-tenant:${id}`,
        fetched_at: observed, quality: 'partial', confidence: 40 }],
    };
  }
  return {
    contract_version: '1.0', company_id: 'synthetic-tenant', audience_role,
    created_at: observed,
    sections: {
      red_alerts: [entry('red_alerts', 'risk', '1 at-risk accounts, 1 dormant accounts, 1 critical inventory alert (snapshot only)')],
      yellow_alerts: [entry('yellow_alerts', 'watch', '2 slowing accounts and 0 low-stock alerts (snapshot only)')],
      opportunities: [],
      scoreboard: [entry('scoreboard', 'score', 'Last 30-day sales in snapshot: 1200.00 across 12 classified accounts (source not independently verified)')],
      todays_priorities: [],
      actions_taken: [],
    },
    source_coverage: 'dashboard_snapshot_only', complete_digest: false,
    source_quality: 'partial', snapshot_generated_at: observed, snapshot_age_seconds: 60,
  };
}
