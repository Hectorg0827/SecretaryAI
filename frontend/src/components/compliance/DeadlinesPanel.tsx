import React from 'react';
import clsx from 'clsx';
import { Calendar } from 'lucide-react';
import { ComplianceDeadline } from '../../lib/api';

interface DeadlinesPanelProps {
  deadlines: ComplianceDeadline[];
  loading: boolean;
}

function daysUntil(dueDateStr: string): number {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = new Date(dueDateStr);
  due.setHours(0, 0, 0, 0);
  return Math.round((due.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
}

function dueDateColor(days: number): string {
  if (days <= 7) return 'text-red-600 font-semibold';
  if (days <= 30) return 'text-amber-600 font-medium';
  return 'text-green-700';
}

function daysLabel(days: number): string {
  if (days < 0) return `${Math.abs(days)}d overdue`;
  if (days === 0) return 'Today';
  return `${days}d`;
}

export function DeadlinesPanel({ deadlines, loading }: DeadlinesPanelProps) {
  if (loading) {
    return (
      <div className="space-y-2 p-6">
        {[...Array(4)].map((_, i) => (
          <div key={i} className="h-12 bg-slate-100 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  if (deadlines.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-slate-400 gap-3">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center">
          <Calendar className="w-6 h-6 text-slate-300" />
        </div>
        <div className="text-center">
          <p className="text-sm font-medium text-slate-500">No upcoming deadlines</p>
          <p className="text-xs mt-1 text-slate-400">No compliance deadlines in the selected period</p>
        </div>
      </div>
    );
  }

  const sorted = [...deadlines].sort((a, b) => daysUntil(a.due_date) - daysUntil(b.due_date));

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center px-6 py-3 border-b border-slate-100 bg-slate-50">
        <p className="text-sm text-slate-500">
          {deadlines.length} deadline{deadlines.length !== 1 ? 's' : ''} upcoming
        </p>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="bg-white border-b border-slate-100">
            <tr>
              {['Type', 'State', 'Due Date', 'Days Until', 'Frequency', 'Action', 'Notes'].map((h) => (
                <th key={h} className="px-5 py-3 text-left text-xs font-semibold text-slate-400 uppercase tracking-wider whitespace-nowrap">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {sorted.map((deadline, idx) => {
              const days = daysUntil(deadline.due_date);
              return (
                <tr key={idx} className="hover:bg-slate-50 transition-colors">
                  <td className="px-5 py-3.5">
                    <div className="text-sm font-semibold text-slate-800">{deadline.deadline_type}</div>
                  </td>
                  <td className="px-5 py-3.5">
                    <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-slate-100 text-slate-700 text-xs font-mono font-semibold">
                      {deadline.state_code}
                    </span>
                  </td>
                  <td className={clsx('px-5 py-3.5 text-sm', dueDateColor(days))}>
                    {deadline.due_date}
                  </td>
                  <td className={clsx('px-5 py-3.5 text-sm', dueDateColor(days))}>
                    {daysLabel(days)}
                  </td>
                  <td className="px-5 py-3.5 text-sm text-slate-600">
                    {deadline.frequency}
                  </td>
                  <td className="px-5 py-3.5 text-sm text-slate-600">
                    {deadline.action}
                  </td>
                  <td className="px-5 py-3.5 text-sm text-slate-500 max-w-[200px]">
                    <span className="truncate block" title={deadline.notes}>
                      {deadline.notes}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
