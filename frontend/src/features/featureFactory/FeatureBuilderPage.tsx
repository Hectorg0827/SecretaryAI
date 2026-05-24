// =============================================================================
// FeatureBuilderPage
// =============================================================================
// The "tell me what you need" experience. Walks the user through the safe flow:
//   1. describe   -> 2. approve capabilities -> 3. preview (dry run) -> watching
// Each step maps 1:1 to a backend endpoint and an audit event.
// =============================================================================

import { useState } from "react";
import { featureFactoryApi } from "./api";
import { CapabilityApprovalCard } from "./CapabilityApprovalCard";
import { DryRunPreview } from "./DryRunPreview";
import type { Capability, CreateFeatureResponse, PlannedAction } from "./types";

type Step = "describe" | "approve" | "preview" | "done";

const EXAMPLES = [
  "Flag any invoice over $5,000 for my approval",
  "Alert me when a customer hasn't ordered in 90 days",
  "Every morning, summarize orders that shipped late",
  "Tag customers in Florida who are over their credit limit",
];

export function FeatureBuilderPage() {
  const [step, setStep] = useState<Step>("describe");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [featureId, setFeatureId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [capabilities, setCapabilities] = useState<Capability[]>([]);
  const [clarifying, setClarifying] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number | undefined>();
  const [planned, setPlanned] = useState<PlannedAction[]>([]);

  async function handleDescribe() {
    setBusy(true);
    setError(null);
    try {
      const res: CreateFeatureResponse = await featureFactoryApi.createFeature(text);
      if (!res.ok) {
        const msg =
          res.validation?.issues?.map((i) => i.message).join(" ") ||
          "I couldn't safely build that. Try describing it a little differently.";
        setError(msg);
        return;
      }
      setFeatureId(res.feature_id!);
      setName(res.feature_id ? text.slice(0, 60) : "New feature");
      setDescription(res.description ?? "");
      setCapabilities(res.declared_capabilities ?? []);
      setClarifying(res.clarifying_question ?? null);
      setConfidence(res.confidence);
      setStep("approve");
    } catch (e: any) {
      setError(e.message ?? "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    if (!featureId) return;
    setBusy(true);
    try {
      await featureFactoryApi.approveCapabilities(featureId);
      const res = await featureFactoryApi.dryRun(featureId);
      setPlanned(res.planned_actions);
      setStep("preview");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    if (featureId) await featureFactoryApi.rejectFeature(featureId).catch(() => {});
    resetAll();
  }

  async function handleLooksGood() {
    if (!featureId) return;
    setBusy(true);
    try {
      await featureFactoryApi.promoteObserve(featureId);
      setStep("done");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  function resetAll() {
    setStep("describe");
    setText("");
    setFeatureId(null);
    setCapabilities([]);
    setPlanned([]);
    setError(null);
  }

  return (
    <div className="min-h-screen bg-slate-50 px-4 py-10">
      <div className="mx-auto max-w-2xl">
        <header className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-slate-900">Build a new feature</h1>
          <p className="mt-1 text-slate-500">
            Describe what you need in plain English. I'll build it, show you exactly
            what it does, and never act without your OK.
          </p>
        </header>

        {error && (
          <div className="mb-4 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
            {error}
          </div>
        )}

        {step === "describe" && (
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={4}
              placeholder="e.g. Flag any invoice over $5,000 for my approval"
              className="w-full resize-none rounded-xl border border-slate-200 p-4 text-slate-800 outline-none focus:border-slate-400"
            />
            <div className="mt-3 flex flex-wrap gap-2">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => setText(ex)}
                  className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1 text-xs text-slate-600 hover:bg-slate-100"
                >
                  {ex}
                </button>
              ))}
            </div>
            <div className="mt-5 flex justify-end">
              <button
                onClick={handleDescribe}
                disabled={busy || text.trim().length < 3}
                className="rounded-lg bg-slate-900 px-5 py-2 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
              >
                {busy ? "Thinking…" : "Build it"}
              </button>
            </div>
          </div>
        )}

        {step === "approve" && (
          <CapabilityApprovalCard
            featureName={name}
            description={description}
            capabilities={capabilities}
            clarifyingQuestion={clarifying}
            confidence={confidence}
            onApprove={handleApprove}
            onReject={handleReject}
            busy={busy}
          />
        )}

        {step === "preview" && (
          <DryRunPreview
            plannedActions={planned}
            onLooksGood={handleLooksGood}
            onNotRight={handleReject}
            busy={busy}
          />
        )}

        {step === "done" && (
          <div className="rounded-2xl border border-emerald-200 bg-white p-8 text-center shadow-sm">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-2xl">
              ✓
            </div>
            <h2 className="text-xl font-semibold text-slate-900">
              It's watching now
            </h2>
            <p className="mx-auto mt-2 max-w-md text-slate-600">
              This feature will quietly run and log what it would do. Once you've
              seen it get a few right, you can let it act for real from the
              feature list.
            </p>
            <button
              onClick={resetAll}
              className="mt-6 rounded-lg bg-slate-900 px-5 py-2 text-sm font-semibold text-white hover:bg-slate-800"
            >
              Build another
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
