// =============================================================================
// FeatureRegistryPage
// =============================================================================
// The control room. Lists every feature, shows where each one is in its
// trust journey (watching -> earned trust -> active), and gives the user the
// controls that matter: promote to act, turn off, and the master kill switch.
// =============================================================================

import { useEffect, useState } from "react";
import { featureFactoryApi } from "./api";
import { STATUS_LABEL, type FeatureStatus, type FeatureView } from "./types";

const STATUS_STYLE: Record<FeatureStatus, string> = {
  draft: "bg-slate-100 text-slate-600",
  pending_capability_approval: "bg-amber-100 text-amber-700",
  dry_run_ready: "bg-sky-100 text-sky-700",
  observe: "bg-indigo-100 text-indigo-700",
  active: "bg-emerald-100 text-emerald-700",
  disabled: "bg-slate-200 text-slate-600",
  failed: "bg-rose-100 text-rose-700",
  rejected: "bg-slate-100 text-slate-400",
};

export function FeatureRegistryPage() {
  const [features, setFeatures] = useState<FeatureView[]>([]);
  const [loading, setLoading] = useState(true);
  const [killed, setKilled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      setFeatures(await featureFactoryApi.listFeatures());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function promote(f: FeatureView) {
    try {
      await featureFactoryApi.promoteActive(f.id);
      await load();
    } catch (e: any) {
      setError(e.message); // e.g. "needs N successful observe runs"
    }
  }

  async function toggleKillSwitch() {
    const next = !killed;
    await featureFactoryApi.setKillSwitch(!next ? true : false);
    setKilled(next);
  }

  return (
    <div className="min-h-screen bg-slate-50 px-4 py-10">
      <div className="mx-auto max-w-3xl">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Your features</h1>
            <p className="text-slate-500">Everything SecretaryAI built for you.</p>
          </div>
          <button
            onClick={toggleKillSwitch}
            className={`rounded-lg px-4 py-2 text-sm font-semibold ${
              killed
                ? "bg-rose-600 text-white hover:bg-rose-500"
                : "border border-rose-200 text-rose-600 hover:bg-rose-50"
            }`}
            title="Instantly stop every generated feature"
          >
            {killed ? "Features paused — resume" : "Emergency stop"}
          </button>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        {loading ? (
          <div className="py-16 text-center text-slate-400">Loading…</div>
        ) : features.length === 0 ? (
          <div className="rounded-2xl border border-slate-200 bg-white py-16 text-center text-slate-500">
            No features yet. Build your first one to get started.
          </div>
        ) : (
          <ul className="space-y-3">
            {features.map((f) => {
              const trustPct = Math.min(
                100,
                Math.round((f.successful_observe_runs / Math.max(1, f.observe_runs_required)) * 100)
              );
              const trustReady = f.successful_observe_runs >= f.observe_runs_required;
              return (
                <li
                  key={f.id}
                  className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
                >
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="font-semibold text-slate-900">{f.name}</div>
                      <p className="mt-0.5 text-sm text-slate-500">{f.description}</p>
                    </div>
                    <span
                      className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_STYLE[f.status]}`}
                    >
                      {STATUS_LABEL[f.status]}
                    </span>
                  </div>

                  {f.status === "observe" && (
                    <div className="mt-4">
                      <div className="mb-1 flex items-center justify-between text-xs text-slate-500">
                        <span>
                          Building trust · {f.successful_observe_runs} of{" "}
                          {f.observe_runs_required} clean runs
                        </span>
                        <span>{trustPct}%</span>
                      </div>
                      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-indigo-500 transition-all"
                          style={{ width: `${trustPct}%` }}
                        />
                      </div>
                      <button
                        onClick={() => promote(f)}
                        disabled={!trustReady}
                        className="mt-3 rounded-lg bg-emerald-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {trustReady
                          ? "Let it act for real"
                          : "Keep watching until trust is earned"}
                      </button>
                    </div>
                  )}

                  {f.status === "failed" && (
                    <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
                      This was switched off automatically after repeated problems.
                      Review its history before turning it back on.
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
