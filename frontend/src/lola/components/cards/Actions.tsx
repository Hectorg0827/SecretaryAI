import { useState } from 'react';
import { CheckCircle2, Clock, Loader2, Lock, ShieldAlert, XCircle, CloudOff, Info, ShieldCheck } from 'lucide-react';
import type { ActionPreview, Notice, Receipt, LolaLanguage } from '../../contract';
import { t } from '../../persona';

const RISK = {
  low: 'text-lola-ok border-lola-ok/30',
  medium: 'text-amber-300 border-amber-300/30',
  high: 'text-lola-danger border-lola-danger/40',
} as const;

interface ActionProps {
  action: ActionPreview;
  receipt?: Receipt;
  lang: LolaLanguage;
  onApprove: () => void;
  onReject: () => void;
}

/**
 * Preview → (approve) → executing → receipt. The "done" state appears ONLY
 * for a verified, executed receipt.
 */
export function ActionCard({ action, receipt, lang, onApprove, onReject }: ActionProps) {
  const [confirming, setConfirming] = useState(false);
  const es = lang === 'es';
  const high = action.risk === 'high';

  return (
    <section
      aria-label={`${es ? 'Acción propuesta' : 'Proposed action'}: ${action.title}`}
      data-action-status={action.status}
      className="rounded-xl border border-lola-gold/30 bg-gradient-to-b from-lola-gold/[0.07] to-transparent p-3.5 sm:p-4"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-[0.12em] text-lola-gold">{es ? 'Acción propuesta' : 'Proposed action'}</p>
          <h3 className="mt-0.5 text-[14px] font-semibold text-lola-text">{action.title}</h3>
          <p className="mt-0.5 text-[13px] text-lola-mute">{action.summary}</p>
        </div>
        <span className={`flex-shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${RISK[action.risk]}`}>
          {es ? { low: 'riesgo bajo', medium: 'riesgo medio', high: 'riesgo alto' }[action.risk] : `${action.risk} risk`}
        </span>
      </div>

      {action.fields.length > 0 && (
        <dl className="mt-3 space-y-1 text-[13px]">
          {action.fields.map((f) => (
            <div key={f.label} className="flex gap-2">
              <dt className="text-lola-mute">{f.label}:</dt>
              <dd className="text-lola-text">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="mt-3" aria-live="polite">
        {action.status === 'proposed' && action.can_approve && (
          confirming || !high ? (
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={onApprove}
                className="inline-flex min-h-[40px] items-center gap-1.5 rounded-lg bg-lola-gold px-3.5 text-[13px] font-semibold text-lola-ink hover:bg-lola-amber focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lola-amber"
              >
                <ShieldCheck size={15} aria-hidden />{high ? (es ? 'Confirmar' : 'Confirm') : t('approve', lang)}
              </button>
              <button
                type="button"
                onClick={() => { setConfirming(false); onReject(); }}
                className="min-h-[40px] rounded-lg border border-lola-line px-3.5 text-[13px] text-lola-text hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber"
              >
                {t('reject', lang)}
              </button>
            </div>
          ) : (
            <button type="button" onClick={() => setConfirming(true)} className="min-h-[40px] rounded-lg border border-lola-gold/50 px-3.5 text-[13px] font-semibold text-lola-amber hover:bg-lola-gold/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-lola-amber">
              {es ? 'Revisar y aprobar…' : 'Review & approve…'}
            </button>
          )
        )}

        {action.status === 'proposed' && !action.can_approve && (
          <p className="flex items-center gap-1.5 text-[13px] text-lola-mute"><Lock size={14} aria-hidden />{action.approval_note ?? (es ? 'Requiere aprobación de un gerente.' : 'Needs manager approval.')}</p>
        )}

        {action.status === 'executing' && (
          <p className="flex items-center gap-1.5 text-[13px] text-lola-amber"><Loader2 size={14} className="animate-spin motion-reduce:animate-none" aria-hidden />{es ? 'Ejecutando… aún no está confirmado.' : 'Running… not confirmed yet.'}</p>
        )}

        {action.status === 'rejected' && (
          <p className="flex items-center gap-1.5 text-[13px] text-lola-mute"><XCircle size={14} aria-hidden />{es ? 'Descartado. No se ejecutó nada.' : 'Dismissed. Nothing was run.'}</p>
        )}

        {receipt && <ReceiptView receipt={receipt} lang={lang} />}
      </div>
    </section>
  );
}

export function ReceiptView({ receipt, lang }: { receipt: Receipt; lang: LolaLanguage }) {
  const es = lang === 'es';
  const done = receipt.status === 'executed' && receipt.verified;
  const failed = receipt.status === 'failed';
  const Icon = done ? CheckCircle2 : failed ? XCircle : Clock;
  const title = done
    ? (es ? 'Hecho y verificado' : 'Done · verified')
    : failed
      ? (es ? 'No se completó' : 'Not completed')
      : (es ? 'Enviado · pendiente de confirmación' : 'Submitted · awaiting confirmation');
  return (
    <div
      role="status"
      data-receipt={done ? 'verified' : failed ? 'failed' : 'unverified'}
      className={`mt-2 rounded-lg border px-3 py-2 text-[13px] ${done ? 'border-lola-ok/30 bg-lola-ok/5 text-lola-text' : failed ? 'border-lola-danger/40 bg-lola-danger/5 text-lola-text' : 'border-amber-300/30 bg-amber-300/5 text-lola-text'}`}
    >
      <p className={`flex items-center gap-1.5 font-semibold ${done ? 'text-lola-ok' : failed ? 'text-lola-danger' : 'text-amber-300'}`}>
        <Icon size={14} aria-hidden />{title}
      </p>
      <p className="mt-0.5">{receipt.message}</p>
      {(receipt.reference || receipt.executed_at) && (
        <p className="mt-0.5 text-[11px] text-lola-mute">
          {receipt.reference && <>{es ? 'Referencia' : 'Reference'} {receipt.reference}</>}
          {receipt.executed_at && <> · {new Date(receipt.executed_at).toLocaleTimeString()}</>}
        </p>
      )}
    </div>
  );
}

const NOTICE = {
  permission_denied: { icon: ShieldAlert, cls: 'border-lola-line bg-white/[0.03] text-lola-text', label: { en: 'Not available for your role', es: 'No disponible para tu rol' } },
  connector_outage: { icon: CloudOff, cls: 'border-lola-danger/40 bg-lola-danger/5 text-lola-text', label: { en: 'Connection down', es: 'Conexión caída' } },
  data_unavailable: { icon: CloudOff, cls: 'border-amber-300/30 bg-amber-300/5 text-lola-text', label: { en: 'Data unavailable', es: 'Datos no disponibles' } },
  info: { icon: Info, cls: 'border-lola-line bg-white/[0.03] text-lola-text', label: { en: 'Note', es: 'Nota' } },
} as const;

export function NoticeView({ notice, lang }: { notice: Notice; lang: LolaLanguage }) {
  const n = NOTICE[notice.kind];
  const Icon = n.icon;
  return (
    <div role="status" data-notice={notice.kind} className={`flex items-start gap-2 rounded-xl border px-3 py-2.5 text-[13px] ${n.cls}`}>
      <Icon size={16} className="mt-0.5 flex-shrink-0 text-lola-amber" aria-hidden />
      <div>
        <p className="font-semibold">{n.label[lang]}{notice.connector ? ` · ${notice.connector}` : ''}</p>
        <p className="text-lola-text/85">{notice.message}</p>
      </div>
    </div>
  );
}
