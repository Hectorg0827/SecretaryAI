import React from 'react';

/**
 * Minimal, safe rendering for Lola's prose: paragraphs, bullet lists and
 * **bold**. No HTML injection — everything is rendered as React text.
 */
export function Markdownish({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  return (
    <>
      {blocks.map((b, i) => {
        const lines = b.split('\n');
        if (lines.every((l) => /^\s*[-•*]\s+/.test(l))) {
          return (
            <ul key={i} className="list-disc pl-5 space-y-1">
              {lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-•*]\s+/, ''))}</li>)}
            </ul>
          );
        }
        return <p key={i} className="whitespace-pre-wrap">{inline(b)}</p>;
      })}
    </>
  );
}

function inline(s: string): React.ReactNode[] {
  return s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={i} className="font-semibold text-lola-glow">{part.slice(2, -2)}</strong>
      : <React.Fragment key={i}>{part}</React.Fragment>,
  );
}
