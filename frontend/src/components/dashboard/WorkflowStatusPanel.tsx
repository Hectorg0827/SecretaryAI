import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { verifiedWorkflowRuns, type DashboardWorkflowRun } from '../../lib/sourceEvidence';

const STATUS_COLORS: Record<string, string> = {
  running: 'bg-blue-100 text-blue-700',
  awaiting_approval: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
};

export function WorkflowStatusPanel() {
  const [runs, setRuns] = useState<DashboardWorkflowRun[]>([]);
  const [error, setError] = useState(false);

  useEffect(() => {
    api.get<unknown>('/api/workflows/')
      .then((response) => {
        const verified = verifiedWorkflowRuns(response);
        if (!verified) throw new Error('Workflow status source evidence is incomplete');
        setRuns(verified.workflows);
        setError(false);
      })
      .catch(() => { setRuns([]); setError(true); });
  }, []);

  if (error) return <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
    Workflow source unavailable. Execution status is unknown.
  </div>;
  if (!runs.length) return null;

  return (
    <div className="bg-white rounded-xl border border-slate-100 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-100">
        <span className="text-sm font-semibold text-slate-700">Active Workflows</span>
      </div>
      <div className="divide-y divide-slate-100">
        {runs.map(run => (
          <div key={run.run_id} className="px-4 py-3">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-slate-700">{run.workflow_name.replace(/_/g, ' ')}</p>
                <p className="text-xs text-slate-500 mt-0.5">Step {run.current_step}/{run.total_steps}: {run.current_step_name}</p>
              </div>
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLORS[run.status] || 'bg-slate-100 text-slate-600'}`}>
                {run.status.replace(/_/g, ' ')}
              </span>
            </div>
            <div className="mt-2 h-1 bg-slate-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-blue-400 rounded-full transition-all"
                style={{ width: `${(run.current_step / run.total_steps) * 100}%` }}
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
