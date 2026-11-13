// @vitest-environment happy-dom
import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { LionUIIcon, type LionUIIconKind } from './LionUIIcon';

const kinds: LionUIIconKind[] = [
  'home', 'inbox', 'work', 'dashboard', 'finance', 'accounts',
  'inventory', 'insights', 'logistics', 'compliance', 'connectors',
  'audit', 'diagnostics', 'settings', 'security', 'user', 'online',
  'offline', 'logout', 'arrow', 'chevron', 'history', 'microphone',
  'microphone-off', 'voice', 'voice-off', 'notification', 'calendar',
  'document', 'search',
];

describe('Lion BI proprietary vector pictograms', () => {
  it('renders every navigation and control icon without external imagery', () => {
    expect(kinds).toHaveLength(30);
    for (const kind of kinds) {
      const markup = renderToStaticMarkup(<LionUIIcon kind={kind} size={20} />);
      expect(markup).toContain('<svg');
      expect(markup).toContain('viewBox="0 0 24 24"');
      expect(markup).toContain('aria-hidden="true"');
      expect(markup).toContain('width="20"');
      expect(markup).toMatch(/<(path|circle|rect) /);
      expect(markup).not.toContain('<img');
    }
  });
});
