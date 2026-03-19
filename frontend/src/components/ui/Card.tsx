import React from 'react';
import clsx from 'clsx';

interface CardProps {
  className?: string;
  children: React.ReactNode;
  onClick?: () => void;
  hover?: boolean;
}

export function Card({ className, children, onClick, hover }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={clsx(
        'bg-white rounded-xl border border-slate-200 shadow-card',
        hover && 'cursor-pointer hover:border-slate-300 hover:shadow-card-md transition-all',
        className,
      )}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  count,
  action,
  className,
}: {
  title: string;
  count?: number | string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx('flex items-center justify-between px-5 py-4 border-b border-slate-100', className)}>
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
        {count !== undefined && (
          <span className="text-xs font-medium bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
            {count}
          </span>
        )}
      </div>
      {action && <div className="text-slate-400">{action}</div>}
    </div>
  );
}
