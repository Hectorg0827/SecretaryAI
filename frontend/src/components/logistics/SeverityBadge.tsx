import React from 'react';
import clsx from 'clsx';

export type Severity = 'info' | 'warning' | 'alert' | 'critical';

const SEVERITY_STYLES: Record<Severity, string> = {
  info:     'bg-blue-100 text-blue-700',
  warning:  'bg-amber-100 text-amber-700',
  alert:    'bg-orange-100 text-orange-700',
  critical: 'bg-red-100 text-red-700',
};

const SEVERITY_LABELS: Record<Severity, string> = {
  info:     'Info',
  warning:  'Warning',
  alert:    'Alert',
  critical: 'Critical',
};

interface SeverityBadgeProps {
  level: Severity;
  className?: string;
}

export function SeverityBadge({ level, className }: SeverityBadgeProps) {
  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold',
        SEVERITY_STYLES[level],
        className,
      )}
    >
      {SEVERITY_LABELS[level]}
    </span>
  );
}
