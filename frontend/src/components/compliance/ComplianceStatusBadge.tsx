import React from 'react';
import clsx from 'clsx';
import { CheckCircle2, AlertTriangle, AlertCircle } from 'lucide-react';
import { ComplianceStatus } from '../../lib/api';

type OverallStatus = ComplianceStatus['overall'];

const STATUS_STYLES: Record<OverallStatus, string> = {
  ok:       'bg-green-100 text-green-700',
  warning:  'bg-amber-100 text-amber-700',
  critical: 'bg-red-100 text-red-700',
};

const STATUS_LABELS: Record<OverallStatus, string> = {
  ok:       'Compliant',
  warning:  'Warning',
  critical: 'Critical',
};

const STATUS_ICONS: Record<OverallStatus, React.ElementType> = {
  ok:       CheckCircle2,
  warning:  AlertTriangle,
  critical: AlertCircle,
};

interface ComplianceStatusBadgeProps {
  status: OverallStatus;
  className?: string;
}

export function ComplianceStatusBadge({ status, className }: ComplianceStatusBadgeProps) {
  const Icon = STATUS_ICONS[status];
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-sm font-semibold',
        STATUS_STYLES[status],
        className,
      )}
    >
      <Icon size={14} />
      {STATUS_LABELS[status]}
    </span>
  );
}
