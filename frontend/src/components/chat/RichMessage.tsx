import type { ReactNode } from 'react';

/**
 * Render Lola's Markdown as React elements, never injecting generated HTML.
 */
type Block =
  | { type: 'heading'; level: 2 | 3 | 4; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; ordered: boolean; items: string[]; start: number }
  | { type: 'table'; columns: string[]; rows: string[][] }
  | { type: 'quote'; text: string }
  | { type: 'code'; text: string; language: string }
  | { type: 'rule' };

function splitTable(row: string): string[] {
  return row.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((cell) => cell.trim());
}
function tableSeparator(row: string): boolean {
  return /^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(row);
}
function marker(line: string): { ordered: boolean; start: number; text: string } | null {
  const bullet = /^\s*[-*+]\s+(.+)$/.exec(line);
  if (bullet) return { ordered: false, start: 1, text: bullet[1] };
  const number = /^\s*(\d+)[.)]\s+(.+)$/.exec(line);
  if (number) return { ordered: true, start: Number(number[1]), text: number[2] };
  return null;
}
function special(line: string, next?: string): boolean {
  const trimmed = line.trim();
  return /^#{1,6}\s/.test(trimmed) || /^[-*_](?:\s*[-*_]){2,}\s*$/.test(trimmed)
    || /^```/.test(trimmed) || /^>\s?/.test(trimmed)
    || marker(line) !== null || (!!next && line.includes('|') && tableSeparator(next));
}

export function parseLolaMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const raw = lines[i];
    const line = raw.trim();
    if (!line) { i++; continue; }
    if (line.startsWith('```')) {
      const language = line.slice(3).trim().split(/\s+/)[0].replace(/[^\w-]/g, '');
      const code: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) code.push(lines[i++]);
      if (i < lines.length) i++;
      blocks.push({ type: 'code', language, text: code.join('\n') });
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      blocks.push({ type: 'heading', level: Math.min(4, Math.max(2, heading[1].length)) as 2 | 3 | 4, text: heading[2] });
      i++; continue;
    }
    if (/^[-*_](?:\s*[-*_]){2,}\s*$/.test(line)) { blocks.push({ type: 'rule' }); i++; continue; }
    if (raw.includes('|') && i + 1 < lines.length && tableSeparator(lines[i + 1])) {
      const columns = splitTable(raw);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().includes('|')) {
        const row = splitTable(lines[i++]);
        rows.push(columns.map((_, column) => row[column] ?? ''));
      }
      blocks.push({ type: 'table', columns, rows }); continue;
    }
    if (/^>\s?/.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) quoted.push(lines[i++].trim().replace(/^>\s?/, ''));
      blocks.push({ type: 'quote', text: quoted.join(' ') }); continue;
    }
    const current = marker(raw);
    if (current) {
      const items: string[] = [];
      const ordered = current.ordered;
      const start = current.start;
      while (i < lines.length) {
        const item = marker(lines[i]);
        if (!item || item.ordered !== ordered) break;
        items.push(item.text);
        i++;
        while (i < lines.length && /^ {2,}\S/.test(lines[i]) && !marker(lines[i])) {
          items[items.length - 1] += ' ' + lines[i++].trim();
        }
      }
      blocks.push({ type: 'list', ordered, start, items }); continue;
    }
    const paragraph: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() && !special(lines[i], lines[i + 1])) paragraph.push(lines[i++].trim());
    blocks.push({ type: 'paragraph', text: paragraph.join(' ') });
  }
  return blocks;
}

const TOKEN = /(\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|\*\*(.+?)\*\*|__(.+?)__|`([^`]+)`|\*([^*\n]+)\*)/g;
function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN)) {
    const index = match.index ?? 0;
    if (index > last) out.push(text.slice(last, index));
    const key = String(index) + '-' + out.length;
    if (match[2] && match[3]) out.push(<a key={key} href={match[3]} target="_blank" rel="noopener noreferrer">{match[2]}</a>);
    else if (match[4] || match[5]) out.push(<strong key={key}>{match[4] || match[5]}</strong>);
    else if (match[6]) out.push(<code key={key}>{match[6]}</code>);
    else if (match[7]) out.push(<em key={key}>{match[7]}</em>);
    last = index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function RichMessage({ content }: { content: string }) {
  const blocks = parseLolaMarkdown(content);
  return (
    <div className="lola-rich">
      {blocks.map((block, i) => {
        const key = i;
        switch (block.type) {
          case 'heading':
            if (block.level === 2) return <h2 key={key}>{inline(block.text)}</h2>;
            if (block.level === 3) return <h3 key={key}>{inline(block.text)}</h3>;
            return <h4 key={key}>{inline(block.text)}</h4>;
          case 'paragraph': return <p key={key}>{inline(block.text)}</p>;
          case 'rule': return <hr key={key} />;
          case 'quote': return <blockquote key={key}>{inline(block.text)}</blockquote>;
          case 'list': {
            const entries = block.items.map((item, idx) => <li key={idx}>{inline(item)}</li>);
            return block.ordered ? <ol key={key} start={block.start}>{entries}</ol> : <ul key={key}>{entries}</ul>;
          }
          case 'table': return (
            <div className="lola-rich__table-wrap" key={key} tabIndex={0} role="region" aria-label="Business data table">
              <table>
                <thead><tr>{block.columns.map((cell, j) => <th scope="col" key={j}>{inline(cell)}</th>)}</tr></thead>
                <tbody>{block.rows.map((row, j) => <tr key={j}>{row.map((cell, k) => <td key={k}>{inline(cell)}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
          case 'code': return <pre key={key} className="lola-rich__code" aria-label={block.language ? block.language + ' code' : 'Code block'}><code>{block.text}</code></pre>;
        }
      })}
    </div>
  );
}
