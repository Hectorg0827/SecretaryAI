import React, { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import { PurchaseCrosscheck } from './PurchaseCrosscheck';

type Eta = { eta: string; source: string; verified: boolean } | null;
type JourneySummary = {
  shipment_id: string;
  current_stage_label: string;
  container_numbers: string[];
  bol_number: string;
  po_numbers: string[];
  destination: string;
  eta: { current: Eta; reported_unverified?: Eta; delay_days: number | null };
  open_exceptions: number;
  pending_evidence: number;
};
type JourneyStage = {
  stage: string;
  order: number;
  label: string;
  status: string;
  responsible_party: string;
  expected_date: string | null;
  actual_date: string | null;
  verified_milestones: Array<{ label: string; source: string; occurred_at: string; verified: boolean }>;
  unverified_reports: Array<{ source: string; occurred_at: string }>;
  exceptions: Array<{ code: string; message: string }>;
};
type JourneyDetail = JourneySummary & { stages: JourneyStage[]; exceptions: Array<{ code: string; message: string }> };
type MonitorAlert = { code: string; severity: string; message: string; shipment_id?: string; suggested_action?: string };

function labelFor(summary: JourneySummary) {
  return summary.container_numbers?.[0] || summary.bol_number || summary.shipment_id;
}
function verifiedEta(eta: JourneySummary['eta'] | undefined): string {
  return eta?.current?.verified ? eta.current.eta : 'Not verified';
}
function description(error: unknown): string {
  return error instanceof Error ? error.message : 'The logistics source is unavailable.';
}

/**
 * Shipment Control Tower — reads only role-scoped, provenance-backed records.
 * Empty responses are explicitly not interpreted as zero real-world shipments.
 * No action here can book freight, send messages, or write inventory/financials.
 */
export function ShipmentControlTower() {
  const [revision, setRevision] = useState(0);
  const [shipments, setShipments] = useState<JourneySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [listError, setListError] = useState('');
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<JourneyDetail | null>(null);
  const [detailError, setDetailError] = useState('');
  const [detailLoading, setDetailLoading] = useState(false);
  const [alerts, setAlerts] = useState<MonitorAlert[]>([]);
  const [alertsError, setAlertsError] = useState('');
  const [alertsLoading, setAlertsLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setListError('');
    api.get<{ shipments: JourneySummary[] }>('/api/logistics/journey/shipments')
      .then((data) => {
        if (!active) return;
        if (!data || !Array.isArray(data.shipments)) throw new Error('Shipment list returned an invalid response.');
        setShipments(data.shipments);
      })
      .catch((err: unknown) => {
        if (active) { setListError(description(err)); setShipments([]); }
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [revision]);

  useEffect(() => {
    let active = true;
    setAlertsLoading(true);
    setAlertsError('');
    api.get<{ alerts: MonitorAlert[] }>('/api/logistics/monitor')
      .then((data) => {
        if (!active) return;
        if (!data || !Array.isArray(data.alerts)) throw new Error('Logistics alert response is invalid.');
        setAlerts(data.alerts);
      })
      .catch((err: unknown) => {
        if (active) { setAlertsError(description(err)); setAlerts([]); }
      })
      .finally(() => { if (active) setAlertsLoading(false); });
    return () => { active = false; };
  }, [revision]);

  useEffect(() => {
    if (!selected) { setDetail(null); setDetailError(''); return; }
    let active = true;
    setDetail(null);
    setDetailError('');
    setDetailLoading(true);
    api.get<JourneyDetail>(`/api/logistics/journey/shipments/${encodeURIComponent(selected)}`)
      .then((data) => {
        if (!active) return;
        if (!data || !Array.isArray(data.stages)) throw new Error('Shipment milestones are unavailable.');
        setDetail(data);
      })
      .catch((err: unknown) => { if (active) setDetailError(description(err)); })
      .finally(() => { if (active) setDetailLoading(false); });
    return () => { active = false; };
  }, [selected, revision]);

  const openExceptions = shipments.reduce((count, s) => count + (s.open_exceptions || 0), 0);
  const pendingEvidence = shipments.reduce((count, s) => count + (s.pending_evidence || 0), 0);
  return <section aria-label="Shipment control tower" className="p-4 sm:p-6 space-y-5">
    <div className="flex flex-wrap gap-3 items-start justify-between">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Shipment control tower</h2>
        <p className="text-sm text-slate-600">14-stage shipment journeys, verified milestones and exceptions.</p>
      </div>
      <button type="button" disabled={loading || alertsLoading} onClick={() => setRevision((x) => x + 1)}
        className="border border-slate-300 rounded-lg px-4 py-2 text-sm hover:bg-slate-100 disabled:opacity-50">Refresh journeys</button>
    </div>
    <p className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
      Official milestones and ETAs require a verified source. Email and AI interpretations are only unverified evidence.
    </p>
    {listError ? <p role="alert" className="text-red-700">Shipment records unavailable: {listError}</p> :
      loading ? <p role="status">Loading recorded shipments…</p> :
      <>
        <dl className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            ['Recorded journeys', shipments.length],
            ['Open exceptions', openExceptions],
            ['Unverified reports', pendingEvidence],
          ].map(([label, value]) => <div key={label} className="rounded-xl border border-slate-200 p-4 bg-white">
            <dt className="text-xs text-slate-600">{label}</dt>
            <dd className="text-2xl font-semibold text-slate-900">{value}</dd>
          </div>)}
        </dl>
        {shipments.length === 0 ? <p className="text-sm text-slate-700">
          No shipment journeys are recorded in this ledger. This does not mean no shipments exist; carrier, broker and warehouse feeds may not be connected.
        </p> : <div className="space-y-2" aria-label="Recorded shipment list">
          {shipments.map((s) => <button type="button" key={s.shipment_id} onClick={() => setSelected(s.shipment_id)}
            aria-pressed={selected === s.shipment_id}
            className={`w-full text-left rounded-xl border p-3 sm:p-4 hover:border-slate-500 focus-visible:ring-2 focus-visible:ring-slate-700 ${selected === s.shipment_id ? 'border-slate-700 bg-slate-50' : 'border-slate-200 bg-white'}`}>
            <span className="flex flex-wrap justify-between gap-2">
              <span className="font-semibold text-slate-900">{labelFor(s)}</span>
              <span className="text-sm text-slate-700">{s.current_stage_label}</span>
            </span>
            <span className="block text-xs text-slate-600 mt-1">
              Verified ETA: {verifiedEta(s.eta)} · Exceptions: {s.open_exceptions} · Evidence to review: {s.pending_evidence}
            </span>
          </button>)}
        </div>}
      </>}
    {detailLoading && <p role="status">Loading shipment milestones…</p>}
    {detailError && <p role="alert" className="text-red-700">Shipment detail unavailable: {detailError}</p>}
    {detail && <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-4" aria-label="Shipment milestone timeline">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="font-semibold text-slate-900">Journey: {labelFor(detail)}</h3>
        <button type="button" onClick={() => setSelected('')} className="text-sm underline text-slate-700">Close detail</button>
      </div>
      <ol className="grid gap-2">
        {detail.stages.map((stage) => <li key={stage.stage} className="rounded-lg border border-slate-200 px-3 py-2">
          <div className="flex flex-wrap justify-between gap-2 text-sm">
            <span className="font-medium text-slate-900">{stage.order}. {stage.label}</span>
            <span className="text-slate-600">{stage.status.replace(/_/g, ' ')}</span>
          </div>
          <p className="text-xs text-slate-600 mt-1">
            Expected: {stage.expected_date || 'Not recorded'} · Actual: {stage.actual_date || 'Not verified'} · Owner: {stage.responsible_party || 'Not assigned'}
          </p>
          {stage.verified_milestones?.map((milestone, i) => <p key={i} className="text-xs text-emerald-800 mt-1">
            Verified: {milestone.label} · {milestone.source} · {milestone.occurred_at}
          </p>)}
          {!!stage.unverified_reports?.length && <p className="text-xs text-amber-800 mt-1">
            {stage.unverified_reports.length} unverified report(s) awaiting human review
          </p>}
          {!!stage.exceptions?.length && <p className="text-xs text-red-800 mt-1">
            {stage.exceptions.length} exception(s) require attention
          </p>}
        </li>)}
      </ol>
      <PurchaseCrosscheck shipmentId={detail.shipment_id} />
    </div>}
    <div className="space-y-2" aria-label="Logistics monitoring">
      <h3 className="font-semibold text-slate-900">Monitoring and exceptions</h3>
      {alertsLoading ? <p role="status">Checking recorded events…</p> :
        alertsError ? <p role="alert" className="text-red-700">Monitoring unavailable: {alertsError}</p> :
        alerts.length === 0 ? <p className="text-sm text-slate-600">
          No alerts found in recorded events. This is not a live carrier or customs all-clear.
        </p> :
        <ul className="space-y-2">{alerts.map((alert, index) => <li key={`${alert.code}-${alert.shipment_id || index}`}
          className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm">
          <span className="font-semibold">{alert.severity}: {alert.code}</span>
          <p>{alert.message}</p>
          {alert.suggested_action && <p className="mt-1 text-slate-700">Next: {alert.suggested_action}</p>}
        </li>)}</ul>}
    </div>
  </section>;
}
