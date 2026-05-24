// =============================================================================
// DryRunPreview
// =============================================================================
// "Here's what I'd do — does this look right?" The safety mechanism that feels
// like helpfulness. Shows the real, computed list of actions the feature would
// take against current data, having changed nothing. The user judges the
// OUTCOME, not the code.
// =============================================================================

import type { PlannedAction } from "./types";

interface Props {
  plannedActions: PlannedAction[];
  onLooksGood: () => void;   // -> promote to observe
  onNotRight: () => void;    // -> back to edit / reject
  busy?: boolean;
}

export function DryRunPreview({ plannedActions, onLooksGood, onNotRight, busy }: Props) {
  const writeCount = plannedActions.filter(
    (a) => a.action_class === "commit" || a.action_class === "destructive"
  ).length;

  return (
    <div className="mx-auto max-w-2xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        Safe preview · nothing has been changed
      </div>
      <h2 className="text-xl font-semibold text-slate-900">
        Here's what this feature would do right now
      </h2>

      {plannedActions.length === 0 ? (
        <div className="mt-4 rounded-xl bg-slate-50 px-4 py-6 text-center text-slate-500">
          Nothing matched today. That's normal — it will act when something does.
        </div>
      ) : (
        <>
          <p className="mt-2 text-slate-600">
            Against your current data, it would take{" "}
            <span className="font-semibold text-slate-900">{plannedActions.length}</span>{" "}
            action{plannedActions.length === 1 ? "" : "s"}
            {writeCount > 0 && (
              <>
                {" "}
                ({writeCount} would ask for your approval before happening)
              </>
            )}
            .
          </p>

          <ul className="mt-4 max-h-72 space-y-2 overflow-auto pr-1">
            {plannedActions.map((a, i) => (
              <li
                key={`${a.target_ref}-${i}`}
                className="flex items-start gap-3 rounded-xl border border-slate-100 bg-slate-50 px-4 py-3"
              >
                <span
                  className={`mt-1 h-2 w-2 shrink-0 rounded-full ${
                    a.requires_approval ? "bg-amber-500" : "bg-emerald-500"
                  }`}
                />
                <div>
                  <div className="text-slate-800">{a.human_summary}</div>
                  {a.requires_approval && (
                    <div className="text-xs text-amber-700">
                      Would wait for your approval
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="mt-6 flex items-center justify-between">
        <button
          onClick={onNotRight}
          disabled={busy}
          className="rounded-lg px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
        >
          Not quite right
        </button>
        <button
          onClick={onLooksGood}
          disabled={busy}
          className="rounded-lg bg-emerald-600 px-5 py-2 text-sm font-semibold text-white hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy ? "Saving…" : "Looks right — start watching"}
        </button>
      </div>

      <p className="mt-3 text-center text-xs text-slate-400">
        Next, it watches quietly and logs what it would do. Once you've seen it get
        a few right, you can let it act for real.
      </p>
    </div>
  );
}
