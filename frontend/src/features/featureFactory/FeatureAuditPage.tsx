// =============================================================================
// FeatureAuditPage
// =============================================================================
// Transparency. Shows the run history for a feature and any actions waiting on
// the user's approval. This is the human-facing window into the tamper-evident
// audit log (Layer 8) and the approval queue (Layers 5 & 7).
// =============================================================================

import { useEffect, useState } from "react";
import { featureFactoryApi } from "./api";
import type { PendingApproval, RunView } from "./types";

interface Props {
  featureId: string;
}

export function FeatureAuditPage({ featureId }: Props) {
  const [runs, setRuns] = useState<RunView[]>([]);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    const [r, a] = await Promise.all([
      featureFactoryApi.listRuns(featureId),
      featureFactoryApi.listApprovals(),
    ]);
    setRuns(r);
    setApprovals(a.filter((x) => x.feature_id === featureId));
    setLoading(false);
  }

  useEffect(() => {
    load();
  }, [featureId]);

  async function decide(approvalId: string, approve: boolean) {
    await featureFactoryApi.decideApproval(approvalId, approve);
    await load();
  }

  const fmt = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString() : "—";

  return (
    <div className="min-h-screen bg-slate-50 px-4 py-10">
      <div className="mx-auto max-w-3xl">
        <h1 className="text-2xl font-bold text-slate-900">Activity & approvals</h1>
        <p className="text-slate-500">Everything this feature has done, on the record.</p>

        {/* Pending approvals first — these need the user. */}
        {approvals.length > 0 && (
          <section className="mt-6">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-amber-700">
              Waiting for your OK
            </h2>
            <ul className="space-y-2">
              {approvals.map((a) => (
                <li
                  key={a.id}
                  className="flex items-center justify-between rounded-xl border border-amber-200 bg-amber-50 px-4 py-3"
                >
                  <span className="text-slate-800">{a.action.human_summary}</span>
                  <span className="flex gap-2">
                    <button
                      onClick={() => decide(a.id, false)}
                      className="rounded-lg px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-white"
                    >
                      Decline
                    </button>
                    <button
                      onClick={() => decide(a.id, true)}
                      className="rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-emerald-500"
                    >
                      Approve
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="mt-8">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-slate-500">
            History
          </h2>
          {loading ? (
            <div className="py-10 text-center text-slate-400">Loading…</div>
          ) : runs.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white py-10 text-center text-slate-500">
              No runs yet.
            </div>
          ) : (
            <ul className="space-y-2">
              {runs.map((run) => (
                <li
                  key={run.id}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="flex items-center gap-2 text-sm font-medium text-slate-800">
                      <ModeBadge mode={run.mode} />
                      {run.result_summary ?? run.status}
                    </span>
                    <span className="text-xs text-slate-400">{fmt(run.started_at)}</span>
                  </div>
                  {run.error && (
                    <div className="mt-1 text-xs text-rose-600">{run.error}</div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function ModeBadge({ mode }: { mode: RunView["mode"] }) {
  const map: Record<RunView["mode"], { label: string; cls: string }> = {
    dry_run: { label: "Preview", cls: "bg-sky-100 text-sky-700" },
    observe: { label: "Watched", cls: "bg-indigo-100 text-indigo-700" },
    act: { label: "Acted", cls: "bg-emerald-100 text-emerald-700" },
  };
  const m = map[mode];
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${m.cls}`}>
      {m.label}
    </span>
  );
}
