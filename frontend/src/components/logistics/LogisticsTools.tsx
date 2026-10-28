import React, { useState } from 'react';
import { api } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';
import {
  BUTTON_PRIMARY, CARD, ErrorNote, Field, INPUT, INVENTORY_VIEW_ROLES, WarningNote, roleAllowed,
} from './logisticsShared';

type BrokerEmailResult = {
  booking_id: string;
  candidate_milestone: string | null;
  candidate_event: { milestone: string; hold_reason: string | null; estimated_demurrage_accruing: boolean } | null;
  message_verified: boolean;
  requires_human_review: boolean;
};

const MILESTONE_TEXT: Record<string, string> = {
  entry_filed: 'Customs entry filed',
  duties_paid: 'Duties paid',
  released: 'Released from customs',
  hold_placed: 'Held by customs',
  arrived_at_port: 'Arrived at port',
};

function milestoneText(value: string): string {
  return MILESTONE_TEXT[value] ?? value.replace(/_/g, ' ');
}

/** Preview what a customs broker email says (POST /customs/parse-broker-email). Nothing is saved. */
export function BrokerEmailParser() {
  const [bookingId, setBookingId] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState<BrokerEmailResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function submit() {
    setResult(null);
    setError(null);
    if (!bookingId.trim() || bookingId.trim().length > 150) { setProblem('Enter the shipment booking reference.'); return; }
    if (!body.trim()) { setProblem('Paste the email text.'); return; }
    setProblem('');
    setLoading(true);
    try {
      const data = await api.post<BrokerEmailResult>('/api/logistics/customs/parse-broker-email', {
        booking_id: bookingId.trim(), subject: subject.slice(0, 500), body: body.slice(0, 25000),
      });
      if (!data || !('candidate_milestone' in data)) throw new Error('Email reading result is unavailable.');
      setResult(data);
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }

  return <form className={CARD} aria-label="Read a customs broker email"
    onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <h3 className="font-semibold text-slate-900">Read a customs broker email</h3>
    <p className="text-sm text-slate-600">Paste a broker email to see which customs step it seems to describe. Nothing is saved or sent.</p>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <Field label="Shipment booking reference" htmlFor="broker-booking">
        <input id="broker-booking" className={INPUT} value={bookingId} maxLength={150} onChange={(event) => setBookingId(event.target.value)} />
      </Field>
      <Field label="Email subject" htmlFor="broker-subject">
        <input id="broker-subject" className={INPUT} value={subject} maxLength={500} onChange={(event) => setSubject(event.target.value)} />
      </Field>
    </div>
    <Field label="Email text" htmlFor="broker-body">
      <textarea id="broker-body" className={INPUT} rows={5} value={body} maxLength={25000} onChange={(event) => setBody(event.target.value)} />
    </Field>
    {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
    <button type="submit" className={BUTTON_PRIMARY} disabled={loading}>{loading ? 'Reading…' : 'Read email'}</button>
    {Boolean(error) && <ErrorNote error={error} fallback="Unable to read the email." />}
    {result && <div className="space-y-2 text-sm" aria-label="Email reading result">
      <p>Possible customs step: <strong>{result.candidate_milestone ? milestoneText(result.candidate_milestone) : 'No clear customs step found'}</strong></p>
      {result.candidate_event?.hold_reason && <p>Hold reason mentioned: {result.candidate_event.hold_reason}</p>}
      {result.candidate_event?.estimated_demurrage_accruing && <p>Port storage charges (demurrage) may be building up.</p>}
      <WarningNote>
        This is a suggestion only. The sender and the shipment link have not been verified, so check the original email
        before acting. The shipment status has not been changed.
      </WarningNote>
    </div>}
  </form>;
}

type SeasonalResult = {
  sku: string;
  modeled_data_quality: string | null;
  days_available: number;
  min_days_needed: number;
  data_months: number | null;
  indices: number[];
  week_start_dates: string[];
};

const QUALITY_TEXT: Record<string, string> = {
  provisional: 'Early estimate (less than 1½ years of history)',
  confirmatory: 'Reasonable estimate (1½ to 2½ years of history)',
  confident: 'Strong estimate (2½ years or more of history)',
  insufficient_data: 'Not enough history',
  no_demand: 'No sales in the history provided',
};

function parseSalesHistory(text: string): { rows: { date: string; units: number }[]; problem: string } {
  const rows: { date: string; units: number }[] = [];
  const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  for (const [index, line] of lines.entries()) {
    const match = /^(\d{4}-\d{2}-\d{2})\s*[,;\t ]\s*(\d+(?:\.\d+)?)$/.exec(line);
    if (!match) {
      if (index === 0 && /date/i.test(line)) continue; // header row
      return { rows: [], problem: `Line ${index + 1} should look like 2026-01-31, 12` };
    }
    rows.push({ date: match[1], units: Number(match[2]) });
  }
  if (rows.length === 0) return { rows, problem: 'Paste at least one day of sales.' };
  if (rows.length > 10000) return { rows: [], problem: 'Paste at most 10,000 days of sales.' };
  return { rows, problem: '' };
}

/** 52-week seasonal demand pattern for one SKU (POST /learning/seasonal-indices). Nothing is saved. */
export function SeasonalIndices() {
  const [sku, setSku] = useState('');
  const [history, setHistory] = useState('');
  const [problem, setProblem] = useState('');
  const [result, setResult] = useState<SeasonalResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  async function submit() {
    setResult(null);
    setError(null);
    if (!sku.trim() || sku.trim().length > 120) { setProblem('Enter the SKU.'); return; }
    const parsed = parseSalesHistory(history);
    if (parsed.problem) { setProblem(parsed.problem); return; }
    setProblem('');
    setLoading(true);
    try {
      const data = await api.post<SeasonalResult>('/api/logistics/learning/seasonal-indices', {
        sku: sku.trim(), daily_sales: parsed.rows,
      });
      if (!data || !Array.isArray(data.indices)) throw new Error('Seasonal pattern is unavailable.');
      setResult(data);
    } catch (reason) {
      setError(reason);
    } finally {
      setLoading(false);
    }
  }

  const peak = result && result.indices.length ? Math.max(...result.indices) : null;
  const trough = result && result.indices.length ? Math.min(...result.indices) : null;

  return <form className={CARD} aria-label="Seasonal demand pattern"
    onSubmit={(event) => { event.preventDefault(); void submit(); }}>
    <h3 className="font-semibold text-slate-900">Seasonal demand pattern</h3>
    <p className="text-sm text-slate-600">
      Paste one line per day (date, cases sold), oldest first, with no missing days — enter 0 for days without sales.
      At least 182 days are needed. Nothing is saved.
    </p>
    <Field label="SKU" htmlFor="seasonal-sku">
      <input id="seasonal-sku" className={INPUT} value={sku} maxLength={120} onChange={(event) => setSku(event.target.value)} />
    </Field>
    <Field label="Daily sales" htmlFor="seasonal-history">
      <textarea id="seasonal-history" className={`${INPUT} font-mono`} rows={6} value={history}
        placeholder={'2026-01-01, 12\n2026-01-02, 9'} onChange={(event) => setHistory(event.target.value)} />
    </Field>
    {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
    <button type="submit" className={BUTTON_PRIMARY} disabled={loading}>{loading ? 'Calculating…' : 'Calculate pattern'}</button>
    {Boolean(error) && <ErrorNote error={error} fallback="Unable to calculate the seasonal pattern." />}
    {result && <div className="space-y-2 text-sm" aria-label="Seasonal pattern result">
      <p>Result quality: <strong>{QUALITY_TEXT[result.modeled_data_quality ?? ''] ?? result.modeled_data_quality ?? 'Unknown'}</strong>
        {' '}· {result.days_available} days ({result.data_months ?? '?'} months) supplied</p>
      {result.indices.length === 0 ? <p className="text-slate-700">
        No seasonal pattern was calculated{result.modeled_data_quality === 'insufficient_data'
          ? `: at least ${result.min_days_needed} days of sales are needed.` : '.'}
      </p> : <>
        <p>Busiest week: ×{peak} · Quietest week: ×{trough} (1.0 = an average week).</p>
        <div className="overflow-x-auto max-h-64">
          <table className="w-full text-left">
            <thead><tr className="text-slate-600"><th className="p-1">Week starting</th><th className="p-1">Demand vs. average</th></tr></thead>
            <tbody>{result.indices.map((value, index) => <tr key={index} className="border-t border-slate-200">
              <td className="p-1">{result.week_start_dates[index] ?? `Week ${index + 1}`}</td><td className="p-1">×{value}</td>
            </tr>)}</tbody>
          </table>
        </div>
        <WarningNote>Weeks are counted from your first sales date. The sales history has not been verified against your records.</WarningNote>
      </>}
    </div>}
  </form>;
}

export function LogisticsTools() {
  const { role } = useAuth();
  if (!roleAllowed(role, INVENTORY_VIEW_ROLES)) {
    return <p className="p-4 sm:p-6 text-sm text-slate-600">These tools are not available for your role.</p>;
  }
  return <section className="p-4 sm:p-6 space-y-4" aria-label="Customs and seasonality tools">
    <BrokerEmailParser />
    <SeasonalIndices />
  </section>;
}
