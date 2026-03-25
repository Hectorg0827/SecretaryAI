import React from 'react';
import clsx from 'clsx';

export type Urgency = 'RED' | 'YELLOW' | 'GREEN';

const URGENCY_STYLES: Record<Urgency, string> = {
  RED:    'bg-red-100 text-red-700',
  YELLOW: 'bg-amber-100 text-amber-700',
  GREEN:  'bg-green-100 text-green-700',
};

interface UrgencyBadgeProps {
  level: Urgency;
  className?: string;
}

export function UrgencyBadge({ level, className }: UrgencyBadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold uppercase tracking-wide',
        URGENCY_STYLES[level],
        className,
      )}
    >
      {level}
    </span>
  );
}
