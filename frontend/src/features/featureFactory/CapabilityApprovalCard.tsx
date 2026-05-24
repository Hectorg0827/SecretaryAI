// =============================================================================
// CapabilityApprovalCard
// =============================================================================
// The single most important screen for trust. The user does NOT read code.
// They read, in plain English, exactly what the feature can touch — and they
// approve THAT. Read capabilities are shown as safe; commit/destructive ones
// are visually flagged so the user understands what carries weight.
// =============================================================================

import type { Capability } from "./types";

interface Props {
  featureName: string;
  description: string;
  capabilities: Capability[];
  clarifyingQuestion?: string | null;
  confidence?: number;
  onApprove: () => void;
  onReject: () => void;
  busy?: boolean;
}

const SCOPE_STYLE: Record<
  Capability["scope"],
  { dot: string; label: string; chip: string }
> = {
  read: { dot: "bg-emerald-500", label: "Read only", chip: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  draft: { dot: "bg-sky-500", label: "Prepares for you", chip: "bg-sky-50 text-sky-700 border-sky-200" },
  commit: { dot: "bg-amber-500", label: "Makes changes", chip: "bg-amber-50 text-amber-700 border-amber-200" },
  destructive: { dot: "bg-rose-500", label: "High impact", chip: "bg-rose-50 text-rose-700 border-rose-200" },
};

export function CapabilityApprovalCard({
  featureName,
  description,
  capabilities,
  clarifyingQuestion,
  confidence,
  onApprove,
  onReject,
  busy,
}: Props) {
  const hasWrites = capabilities.some(
    (c) => c.scope === "commit" || c.scope === "destructive"
  );

  return (
    <div className="mx-auto max-w-2xl rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-slate-400">
        Review before turning on
      </div>
      <h2 className="text-xl font-semibold text-slate-900">{featureName}</h2>
      <p className="mt-2 text-slate-600">{description}</p>

      {typeof confidence === "number" && confidence < 0.6 && (
        <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
          I wasn't fully sure about this one — please read it carefully before approving.
        </div>
      )}

      {clarifyingQuestion && (
        <div className="mt-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900">
          <span className="font-medium">One question first: </span>
          {clarifyingQuestion}
        </div>
      )}

      <div className="mt-5">
        <div className="mb-2 text-sm font-medium text-slate-700">
          Here's exactly what this feature will be allowed to do:
        </div>
        <ul className="space-y-2">
          {capabilities.map((c) => {
            const s = SCOPE_STYLE[c.scope];
            return (
              <li
                key={c.capability_key}
                className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 px-4 py-3"
              >
                <span className="flex items-center gap-3 text-slate-800">
                  <span className={`h-2.5 w-2.5 rounded-full ${s.dot}`} />
                  {c.human_summary}
                </span>
                <span className={`rounded-full border px-2.5 py-0.5 text-xs font-medium ${s.chip}`}>
                  {s.label}
                </span>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="mt-5 rounded-xl bg-slate-900 px-4 py-3 text-sm text-slate-200">
        {hasWrites ? (
          <>
            This feature can make changes — but it will <span className="font-semibold text-white">never</span>{" "}
            do anything risky without showing you first and getting your OK.
          </>
        ) : (
          <>This feature only reads your data. It cannot change anything.</>
        )}
      </div>

      <div className="mt-6 flex items-center justify-end gap-3">
        <button
          onClick={onReject}
          disabled={busy}
          className="rounded-lg px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
        >
          No thanks
        </button>
        <button
          onClick={onApprove}
          disabled={busy}
          className="rounded-lg bg-slate-900 px-5 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
        >
          {busy ? "Saving…" : "Approve these permissions"}
        </button>
      </div>
    </div>
  );
}
