import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { ASSISTANT_NAME, BRAND_LABELS, PLATFORM_NAME } from './brand';
import { LolaGlyph } from '../components/lola/LolaGlyph';

const read = (path: string) => readFileSync(resolve(process.cwd(), path), 'utf8');

describe('Lion BI platform / Lola assistant brand hierarchy', () => {
  it('keeps the names separate, with a single platform app title', () => {
    expect(PLATFORM_NAME).toBe('Lion BI');
    expect(ASSISTANT_NAME).toBe('Lola');
    expect(BRAND_LABELS.home).toBe('Home');
    expect(BRAND_LABELS.assistant).toBe('Lola');
    expect(read('index.html')).toContain('<title>Lion BI</title>');
    const manifest = JSON.parse(read('public/manifest.json')) as { name: string; short_name: string };
    expect(manifest.name).toBe('Lion BI');
    expect(manifest.short_name).toBe('Lion BI');
  });

  it('places the Lion BI logo alone on the home header', () => {
    const home = read('src/pages/LolaHome.tsx');
    expect(home).toContain('<LionBrand showSubtitle={false} />');
    expect(home).not.toContain('LION BI <span>/</span> LOLA');
    expect(home).toContain('BRAND_LABELS.voiceGreeting');
    const sidebar = read('src/components/layout/Sidebar.tsx');
    expect(sidebar).toContain('label: BRAND_LABELS.home');
    expect(sidebar).not.toContain("to: '/chat'");
    expect(read('src/App.tsx')).toContain('<LolaFloatingAssistant />');
  });

  it('uses a bespoke orbital assistant mark rather than a stock sparkle for identity', () => {
    const svg = renderToStaticMarkup(<LolaGlyph size={18} />);
    expect(svg).toContain('<svg');
    expect(svg).toContain('<linearGradient');
    expect(svg).toContain('aria-hidden="true"');
    const assistant = read('src/components/layout/InlineAssistant.tsx');
    expect(assistant).toContain('Ask Lola');
    expect(assistant).not.toContain('AI Assistant');
  });
});
