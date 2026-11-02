import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { ErrorNote, formatDateTime } from './logisticsShared';

type Pipeline = {
  po_number: string;
  current_stage: string | null;
  stage_verified: boolean;
  supplier_id: string | null;
  total_cases: number | null;
  started_at: string | null;
  updated_at: string | null;
};

function stageLabel(stage: string | null): string {
  if (!stage) return 'Not recorded';
  const text = stage.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Legacy shipment-tracking stage (GET /pipeline/{po}); labelled unverified. */
export function PipelineStage({ poNumber }: { poNumber: string }) {
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setPipeline(null);
    setMissing(false);
    setError(null);
    api.get<Pipeline>(`/api/logistics/pipeline/${encodeURIComponent(poNumber)}`).then((data) => {
      if (!data || typeof data !== 'object') throw new Error('Shipment tracking stage is unavailable.');
      if (current) setPipeline(data);
    }).catch((reason: unknown) => {
      if (!current) return;
      if (reason instanceof Error && /procurement cycle not found/i.test(reason.message)) setMissing(true);
      else setError(reason);
    }).finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [poNumber]);

  return <section aria-label="Shipment tracking stage" className="space-y-2">
    <h4 className="font-medium">Shipment tracking stage</h4>
    {loading ? <p role="status" className="text-sm">Loading shipment tracking…</p> :
      error ? <ErrorNote error={error} fallback="Unable to load the shipment tracking stage." /> :
      missing ? <p className="text-sm text-slate-600">No shipment tracking has been recorded for this order.</p> :
      pipeline && <div className="text-sm space-y-1">
        <p>Current stage: <strong>{stageLabel(pipeline.current_stage)}</strong></p>
        <p className="text-slate-600">Last updated: {formatDateTime(pipeline.updated_at)}</p>
        <p className="text-amber-800">
          {pipeline.stage_verified ? 'Stage confirmed.' : 'Not verified: this stage comes from older tracking records and may not match the purchasing ledger above.'}
        </p>
      </div>}
  </section>;
}
