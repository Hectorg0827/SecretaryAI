import { describe, expect, it } from 'vitest';
import { verifiedBriefingToday } from '../../lib/sourceEvidence';
import { fixtureBriefing } from './briefingFixture.test-data';

describe('canonical Morning Briefing v1 source integrity (synthetic only)', () => {
  it('requires exactly six server-scoped sections and partial provenance', () => {
    expect(verifiedBriefingToday(fixtureBriefing())?.sections.scoreboard).toHaveLength(1);
    expect(verifiedBriefingToday({ ...fixtureBriefing(), sections: { red_alerts: [] } })).toBeNull();
    expect(verifiedBriefingToday({ ...fixtureBriefing(), source_quality: 'verified' })).toBeNull();
    expect(verifiedBriefingToday({ ...fixtureBriefing(), complete_digest: true })).toBeNull();
  });

  it('rejects entry role-independent evidence gaps and impossible source claims', () => {
    const baseline = fixtureBriefing();
    const entry = baseline.sections.red_alerts[0];
    expect(verifiedBriefingToday({ ...baseline, sections: {
      ...baseline.sections, red_alerts: [{ ...entry, evidence: [] }],
    } })).toBeNull();
    expect(verifiedBriefingToday({ ...baseline, sections: {
      ...baseline.sections, red_alerts: [{ ...entry, section: 'opportunities' }],
    } })).toBeNull();
    expect(verifiedBriefingToday({ ...baseline, sections: {
      ...baseline.sections, red_alerts: [{ ...entry, status: 'open', evidence: [{
        ...entry.evidence[0], quality: 'unavailable', confidence: 0,
      }] }],
    } })).toBeNull();
  });

  it('rejects invalid or unzoned timestamps and fabricated freshness', () => {
    expect(verifiedBriefingToday({ ...fixtureBriefing(), created_at: '2026-10-26T12:00:00' })).toBeNull();
    expect(verifiedBriefingToday({ ...fixtureBriefing(), snapshot_age_seconds: -1 })).toBeNull();
    expect(verifiedBriefingToday({ ...fixtureBriefing(), snapshot_age_seconds: Infinity })).toBeNull();
    expect(verifiedBriefingToday({ ...fixtureBriefing(), snapshot_age_seconds: 86401 })).toBeNull();
    expect(verifiedBriefingToday({ ...fixtureBriefing(), snapshot_generated_at: '2026-10-24T12:00:00Z' })).toBeNull();
    expect(verifiedBriefingToday({ ...fixtureBriefing(), source_coverage: 'all_sources' })).toBeNull();
  });
});
