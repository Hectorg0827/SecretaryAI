import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { parseLolaMarkdown, RichMessage } from './RichMessage';

describe('Lola formatted executive responses', () => {
  it('renders source headings, emphasis, lists and tables instead of raw markdown', () => {
    const text = [
      '### What needs my attention',
      '',
      '**Inventory risks:** Three items need review.',
      '',
      '- **SKU A:** Low stock',
      '- **SKU B:** Inbound shipment',
      '',
      '| Account | Balance |',
      '| --- | --- |',
      '| Example | $160 |',
    ].join('\n');
    const blocks = parseLolaMarkdown(text);
    expect(blocks.map((block) => block.type)).toEqual([
      'heading', 'paragraph', 'list', 'table',
    ]);
    const html = renderToStaticMarkup(<RichMessage content={text} />);
    expect(html).toContain('<h3>What needs my attention</h3>');
    expect(html).toContain('<strong>Inventory risks:</strong>');
    expect(html).toContain('<ul>');
    expect(html).toContain('<table>');
    expect(html).toContain('<th scope="col">Account</th>');
    expect(html).not.toContain('###');
    expect(html).not.toContain('**');
  });

  it('escapes generated HTML and never turns unsafe schemes into links', () => {
    const html = renderToStaticMarkup(
      <RichMessage content={'<script>alert(1)</script>\n\n[click](javascript:alert(1))'} />,
    );
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('href="javascript:');
  });

  it('supports ordered priorities and quoted context', () => {
    const html = renderToStaticMarkup(
      <RichMessage content={'1. Review receivables\n2. Confirm inventory\n\n> Figures require verification.'} />,
    );
    expect(html).toContain('<ol start="1">');
    expect(html).toContain('<blockquote>');
  });

  it('safely renders incomplete streaming markdown', () => {
    const html = renderToStaticMarkup(<RichMessage content={'### Inventory\n\n- **Low stock'} />);
    expect(html).toContain('<h3>Inventory</h3>');
    expect(html).toContain('Low stock');
  });
});
