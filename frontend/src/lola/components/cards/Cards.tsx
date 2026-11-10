/**
 * Evidence cards rendered inside Lola's answers. Every card shows where its
 * data came from and how fresh it is; any non-`ok` availability renders a
 * clear non-success state instead of numbers.
 */
import React from 'react';
import { Link } from 'react-router-dom';
import {
  ResponsiveContainer, LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, Legend,
} from 'recharts';
import { AlertTriangle, ArrowDownRight, ArrowUpRight, CheckCircle2, Circle, Clock, Lock, Ship, CloudOff } from 'lucide-react';
import type {
  Availability, ChartCard, EvidenceCard, ExplanationCard, MetricCard, TableCard, TimelineCard, Evidence,
} from '../../contract';

/** Validated (dataviz validator, dark surface #131C2E): fixed order, never cycled. */
export const SERIES_COLORS = ['#B0802C', '#5E8BE0'] as const;

function relTime(iso?: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const mins = Math.round((Date.now() - d.getTime()) / 60_000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  if (h < 24) return `${h} h ago`;
  return d.toLocaleDateString();
}

export function EvidenceFooter({ evidence, showTime = true }: { evidence?: Evidence[]; showTime?: boolean }) {
  if (!evidence?.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-lola-mute">
      {evidence.map((e, i) => {
        const when = showTime ? relTime(e.as_of) : null;
        const label = (
          <>
            <span className="text-lola-text/80">{e.source}</span>
            {e.record_ref && <span> · {e.record_ref}</span>}
            {when && <span> · as of {when}</span>}
          </>
        );
        return e.href
          ? <Link key={i} to={e.href} className="underline decoration-lola-line underline-offset-2 hover:text-lola-amber focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber rounded">{label}</Link>
          : <span key={i}>{label}</span>;
      })}
    </div>
  );
}

const AVAIL: Record<Exclude<Availability, 'ok'>, { icon: React.ElementType; text: string; cls: string }> = {
  partial: { icon: AlertTriangle, text: 'Partial data', cls: 'text-amber-300 border-amber-400/30 bg-amber-400/5' },
  stale: { icon: Clock, text: 'May be out of date', cls: 'text-amber-300 border-amber-400/30 bg-amber-400/5' },
  unavailable: { icon: CloudOff, text: 'Data unavailable', cls: 'text-lola-danger border-lola-danger/30 bg-lola-danger/5' },
  forbidden: { icon: Lock, text: 'Restricted', cls: 'text-lola-mute border-lola-line bg-white/5' },
};

export function AvailabilityBanner({ availability, note }: { availability: Availability; note?: string }) {
  if (availability === 'ok') return null;
  const a = AVAIL[availability];
  const Icon = a.icon;
  return (
    <div role="status" data-availability={availability} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-xs ${a.cls}`}>
      <Icon size={14} className="mt-0.5 flex-shrink-0" aria-hidden />
      <span><strong className="font-semibold">{a.text}.</strong> {note}</span>
    </div>
  );
}

function Shell({ card, children }: { card: EvidenceCard; children: React.ReactNode }) {
  const blocked = card.availability === 'unavailable' || card.availability === 'forbidden';
  return (
    <section
      aria-label={card.title}
      data-card-kind={card.kind}
      data-availability={card.availability}
      className="rounded-xl border border-lola-line bg-lola-panel/80 p-3.5 sm:p-4"
    >
      <h3 className="mb-2 text-[13px] font-semibold tracking-wide text-lola-text">{card.title}</h3>
      {card.availability !== 'ok' && <div className="mb-2"><AvailabilityBanner availability={card.availability} note={card.availability_note} /></div>}
      {!blocked && children}
      <EvidenceFooter evidence={card.evidence} showTime={!blocked} />
    </section>
  );
}

function MetricsBody({ card }: { card: MetricCard }) {
  return (
    <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {card.metrics.map((m) => (
        <div key={m.label} className="rounded-lg bg-lola-deep/70 px-3 py-2">
          <dt className="text-[11px] text-lola-mute">{m.label}</dt>
          <dd className="mt-0.5 text-base font-semibold tabular-nums text-lola-text">{m.value}</dd>
          {m.delta && (
            <dd className={`flex items-center gap-0.5 text-[11px] tabular-nums ${m.tone === 'good' ? 'text-lola-ok' : m.tone === 'bad' ? 'text-lola-danger' : 'text-lola-mute'}`}>
              {m.tone === 'good' ? <ArrowUpRight size={11} aria-hidden /> : m.tone === 'bad' ? <ArrowDownRight size={11} aria-hidden /> : null}
              {m.delta}
              <span className="sr-only">{m.tone === 'good' ? '(favourable)' : m.tone === 'bad' ? '(unfavourable)' : ''}</span>
            </dd>
          )}
        </div>
      ))}
    </dl>
  );
}

function fmt(v: number | string, f?: ChartCard['value_format']): string {
  if (typeof v !== 'number') return String(v);
  if (f === 'currency') return v >= 1000 ? `$${(v / 1000).toFixed(v >= 10_000 ? 0 : 1)}k` : `$${v}`;
  if (f === 'percent') return `${v}%`;
  return v.toLocaleString();
}

function ChartBody({ card }: { card: ChartCard }) {
  const axis = { stroke: '#8C97AD', fontSize: 11, tickLine: false, axisLine: false } as const;
  const tooltip = (
    <Tooltip
      cursor={{ stroke: '#24304A', fill: 'rgba(255,255,255,0.04)' }}
      contentStyle={{ background: '#0B1324', border: '1px solid #24304A', borderRadius: 8, fontSize: 12, color: '#E8ECF4' }}
      formatter={(v: number) => fmt(v, card.value_format)}
    />
  );
  const multi = card.series.length > 1;
  return (
    <>
      <div className="h-44 w-full" aria-hidden>
        <ResponsiveContainer width="100%" height="100%">
          {card.chart === 'line' ? (
            <LineChart data={card.data} margin={{ top: 6, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="#24304A" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey={card.x_key} {...axis} />
              <YAxis {...axis} tickFormatter={(v) => fmt(v, card.value_format)} width={48} />
              {tooltip}
              {multi && <Legend wrapperStyle={{ fontSize: 11, color: '#8C97AD' }} />}
              {card.series.map((s, i) => (
                <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={SERIES_COLORS[i % 2]} strokeWidth={2} dot={false} activeDot={{ r: 4, stroke: '#131C2E', strokeWidth: 2 }} />
              ))}
            </LineChart>
          ) : (
            <BarChart data={card.data} margin={{ top: 6, right: 8, bottom: 0, left: -8 }} barGap={2}>
              <CartesianGrid stroke="#24304A" strokeDasharray="2 4" vertical={false} />
              <XAxis dataKey={card.x_key} {...axis} />
              <YAxis {...axis} tickFormatter={(v) => fmt(v, card.value_format)} width={48} />
              {tooltip}
              {multi && <Legend wrapperStyle={{ fontSize: 11, color: '#8C97AD' }} />}
              {card.series.map((s, i) => (
                <Bar key={s.key} dataKey={s.key} name={s.label} fill={SERIES_COLORS[i % 2]} radius={[4, 4, 0, 0]} maxBarSize={22} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      {/* Table view for screen readers (and anyone who prefers numbers). */}
      <table className="sr-only">
        <caption>{card.title}</caption>
        <thead><tr><th>{card.x_key}</th>{card.series.map((s) => <th key={s.key}>{s.label}</th>)}</tr></thead>
        <tbody>
          {card.data.map((row, i) => (
            <tr key={i}><td>{row[card.x_key]}</td>{card.series.map((s) => <td key={s.key}>{fmt(row[s.key], card.value_format)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function TableBody({ card }: { card: TableCard }) {
  return (
    <div className="-mx-1 overflow-x-auto lola-scroll focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber rounded" tabIndex={0} role="region" aria-label={`${card.title} — scrollable table`}>
      <table className="w-full min-w-[320px] text-left text-[13px]">
        <thead>
          <tr className="text-[11px] uppercase tracking-wider text-lola-mute">
            {card.columns.map((c) => (
              <th key={c.key} scope="col" className={`px-1 pb-1.5 font-medium ${c.align === 'right' ? 'text-right' : ''}`}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-lola-line/70">
          {card.rows.map((r, i) => (
            <tr key={i}>
              {card.columns.map((c, j) => {
                const v = r[c.key];
                const Cell = j === 0 ? 'th' : 'td';
                return (
                  <Cell key={c.key} {...(j === 0 ? { scope: 'row' } : {})} className={`px-1 py-1.5 font-normal text-lola-text ${c.align === 'right' ? 'text-right tabular-nums' : ''}`}>
                    {v}
                  </Cell>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {card.total_label && <p className="mt-2 text-right text-[13px] font-semibold text-lola-amber">{card.total_label}</p>}
    </div>
  );
}

const STEP_ICON = { done: CheckCircle2, current: Clock, pending: Circle, exception: AlertTriangle } as const;
const STEP_CLS = {
  done: 'text-lola-ok', current: 'text-lola-amber', pending: 'text-lola-mute', exception: 'text-amber-400',
} as const;
const STEP_SR = { done: 'completed', current: 'in progress', pending: 'not yet', exception: 'exception' } as const;

function TimelineBody({ card }: { card: TimelineCard }) {
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-lola-mute">
        <span className="inline-flex items-center gap-1 text-lola-text"><Ship size={13} aria-hidden />{card.reference}</span>
        {card.carrier && <span>{card.carrier}</span>}
        {card.eta && <span className="font-medium text-lola-amber">{card.eta}</span>}
      </div>
      <ol className="relative ml-2 space-y-3 border-l border-lola-line pl-5">
        {card.steps.map((s, i) => {
          const Icon = STEP_ICON[s.status];
          return (
            <li key={i} className="relative" data-step-status={s.status}>
              <span className={`absolute -left-[29px] top-0 rounded-full bg-lola-panel p-0.5 ${STEP_CLS[s.status]}`}>
                <Icon size={15} aria-hidden />
              </span>
              <div className="flex flex-wrap items-baseline justify-between gap-x-2">
                <span className={`text-[13px] ${s.status === 'pending' ? 'text-lola-mute' : 'text-lola-text'} ${s.status === 'exception' ? 'font-semibold' : ''}`}>
                  {s.label}<span className="sr-only"> — {STEP_SR[s.status]}</span>
                </span>
                {s.at && <time className="text-[11px] tabular-nums text-lola-mute" dateTime={s.at}>{new Date(s.at + 'T12:00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}</time>}
              </div>
              {s.detail && <p className="text-xs text-lola-mute">{s.detail}</p>}
            </li>
          );
        })}
      </ol>
      {!!card.exceptions?.length && (
        <ul className="mt-3 space-y-1">
          {card.exceptions.map((e, i) => (
            <li key={i} className="flex items-start gap-1.5 rounded-md bg-amber-400/10 px-2 py-1.5 text-xs text-amber-200">
              <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" aria-hidden /><span><span className="sr-only">Exception: </span>{e}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ExplanationBody({ card }: { card: ExplanationCard }) {
  return (
    <div>
      <p className="mb-2 text-lg font-semibold tabular-nums text-lola-text">{card.headline}</p>
      <ul className="space-y-1.5">
        {card.drivers.map((d) => (
          <li key={d.label} className="flex items-center justify-between gap-3 text-[13px]">
            <span className="flex items-center gap-1.5 text-lola-text">
              {d.direction === 'up' ? <ArrowUpRight size={14} className="text-lola-ok" aria-hidden /> : <ArrowDownRight size={14} className="text-lola-danger" aria-hidden />}
              {d.label}
            </span>
            <span className="tabular-nums text-lola-mute">{d.impact}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function EvidenceCardView({ card }: { card: EvidenceCard }) {
  return (
    <Shell card={card}>
      {card.kind === 'metrics' && <MetricsBody card={card} />}
      {card.kind === 'chart' && <ChartBody card={card} />}
      {card.kind === 'table' && <TableBody card={card} />}
      {card.kind === 'shipment_timeline' && <TimelineBody card={card} />}
      {card.kind === 'financial_explanation' && <ExplanationBody card={card} />}
    </Shell>
  );
}
