import { describe, expect, it } from 'vitest';
import { dueFollowUps, verifiedOpportunities } from './MorningBriefing';
import type { FeedEvent, FollowUpNote } from '../../lib/api';

const note = (id: string, due_date: string | undefined, done = false): FollowUpNote => ({
  id, text: 'Synthetic QA follow-up', due_date, done, created_at: '2026-10-20T00:00:00Z',
});

const event = (id: string, event_type: string, action_type: string | null = null): FeedEvent => ({
  id, event_type, action_type, title: 'Synthetic QA event', body: '', priority: 'low',
  action_label: null, action_data: {}, entity_name: null, is_read: false,
  created_at: '2026-10-20T00:00:00Z',
});

describe('morning briefing classifications (synthetic fixtures only)', () => {
  it('shows only unfinished due or overdue notes, not future or completed work', () => {
    expect(dueFollowUps([
      note('overdue', '2026-10-22'), note('today', '2026-10-26'),
      note('future', '2026-10-27'), note('completed', '2026-10-20', true), note('undated', undefined),
    ], '2026-10-26').map((item) => item.id)).toEqual(['overdue', 'today']);
  });

  it('does not mislabel low-priority news as verified sales opportunities', () => {
    expect(verifiedOpportunities([
      event('general', 'insight'), event('identified', 'opportunity'),
      event('action', 'insight', 'opportunity'),
    ]).map((item) => item.id)).toEqual(['identified', 'action']);
  });
});
