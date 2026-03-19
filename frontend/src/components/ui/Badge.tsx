import React from 'react';
import clsx from 'clsx';

type Variant = 'high' | 'medium' | 'low' | 'healthy' | 'slowing' | 'at_risk' | 'dormant' |
               'critical' | 'out_of_stock' | 'gray' | 'blue' | 'green' | 'amber' | 'red';

const VARIANT_CLS: Record<Variant, string> = {
  high:        'bg-red-100    text-red-700    border-red-200',
  medium:      'bg-amber-100  text-amber-700  border-amber-200',
  low:         'bg-slate-100  text-slate-500  border-slate-200',
  healthy:     'bg-emerald-100 text-emerald-700 border-emerald-200',
  slowing:     'bg-amber-100  text-amber-700  border-amber-200',
  at_risk:     'bg-orange-100 text-orange-700 border-orange-200',
  dormant:     'bg-red-100    text-red-700    border-red-200',
  critical:    'bg-orange-100 text-orange-700 border-orange-200',
  out_of_stock:'bg-red-100    text-red-700    border-red-200',
  gray:        'bg-slate-100  text-slate-600  border-slate-200',
  blue:        'bg-blue-100   text-blue-700   border-blue-200',
  green:       'bg-emerald-100 text-emerald-700 border-emerald-200',
  amber:       'bg-amber-100  text-amber-700  border-amber-200',
  red:         'bg-red-100    text-red-700    border-red-200',
};

const LABEL: Partial<Record<Variant, string>> = {
  high:        'HIGH',
  medium:      'MED',
  low:         'LOW',
  healthy:     'Healthy',
  slowing:     'Slowing',
  at_risk:     'At Risk',
  dormant:     'Dormant',
  critical:    'Critical',
  out_of_stock:'Out of Stock',
};

interface BadgeProps {
  variant: Variant;
  label?: string;
  className?: string;
}

export function Badge({ variant, label, className }: BadgeProps) {
  return (
    <span className={clsx(
      'inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold border tracking-wide',
      VARIANT_CLS[variant],
      className,
    )}>
      {label ?? LABEL[variant] ?? variant}
    </span>
  );
}
