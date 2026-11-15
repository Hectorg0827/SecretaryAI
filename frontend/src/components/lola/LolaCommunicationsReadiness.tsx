import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import './lola-communications-readiness.css';

type Readiness = {
  source: 'existing_employee_private_tables';
  email_state:
    | 'single_connection_recorded'
    | 'multiple_connections_require_selection'
    | 'no_active_email_connection_recorded';
  whatsapp_state:
    | 'existing_consent_and_ownership_metadata_present'
    | 'connection_pending_or_consent_incomplete'
    | 'no_employee_whatsapp_connection_recorded';
  contact_state: 'not_checked';
  provider_message_coverage_verified: false;
  cross_channel_work_item_verified: false;
  eligible_for_follow_up_engine: false;
  proactive_notifications_enabled_by_this_check: false;
  external_writes_executed: false;
};

const emailLabels: Record<Readiness['email_state'], string> = {
  single_connection_recorded: 'One employee email connection recorded',
  multiple_connections_require_selection: 'Several email connections; selection needed',
  no_active_email_connection_recorded: 'No connected employee mailbox verified',
};
const whatsappLabels: Record<Readiness['whatsapp_state'], string> = {
  existing_consent_and_ownership_metadata_present: 'WhatsApp consent and ownership metadata recorded',
  connection_pending_or_consent_incomplete: 'WhatsApp connection or consent incomplete',
  no_employee_whatsapp_connection_recorded: 'No employee WhatsApp connection recorded',
};

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function valid(value: unknown): value is Readiness {
  return object(value) && value.source === 'existing_employee_private_tables' &&
    (value.email_state === 'single_connection_recorded' ||
      value.email_state === 'multiple_connections_require_selection' ||
      value.email_state === 'no_active_email_connection_recorded') &&
    (value.whatsapp_state === 'existing_consent_and_ownership_metadata_present' ||
      value.whatsapp_state === 'connection_pending_or_consent_incomplete' ||
      value.whatsapp_state === 'no_employee_whatsapp_connection_recorded') &&
    value.contact_state === 'not_checked' &&
    value.provider_message_coverage_verified === false &&
    value.cross_channel_work_item_verified === false &&
    value.eligible_for_follow_up_engine === false &&
    value.proactive_notifications_enabled_by_this_check === false &&
    value.external_writes_executed === false;
}

/** Read-only capability status. This does NOT display employees' private content. */
export function LolaCommunicationsReadiness({ enabled }: { enabled: boolean }) {
  const [status, setStatus] = useState<Readiness | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [loading, setLoading] = useState(enabled);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    setUnavailable(false);
    void api.get<unknown>('/api/lola/chief-of-staff/continuity/readiness')
      .then((value) => {
        if (cancelled) return;
        if (valid(value)) setStatus(value);
        else { setStatus(null); setUnavailable(true); }
      }).catch(() => {
        if (!cancelled) { setStatus(null); setUnavailable(true); }
      }).finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [enabled]);

  if (!enabled) return null;
  return (
    <section className="lola-comms" aria-labelledby="lola-comms-title">
      <div className="lola-comms__heading">
        <p className="lola-home__section-kicker">PRIVATE COMMUNICATIONS · CONNECTION STATUS</p>
        <h3 id="lola-comms-title">Where Lola's follow-up awareness stands</h3>
      </div>
      {loading ? <p role="status">Checking your own communication connection metadata…</p>
        : unavailable || !status
          ? <p role="status">Connection readiness unavailable. Lola cannot verify cross-channel follow-ups.</p>
          : <div className="lola-comms__grid">
              <div>
                <span className="lola-comms__label">Email</span>
                <strong>{emailLabels[status.email_state]}</strong>
              </div>
              <div>
                <span className="lola-comms__label">WhatsApp Business</span>
                <strong>{whatsappLabels[status.whatsapp_state]}</strong>
              </div>
              <div>
                <span className="lola-comms__label">Cross-channel verification</span>
                <strong>Not established</strong>
              </div>
            </div>}
      <p className="lola-comms__disclaimer">
        Read-only status for your own connections. A recorded account or consent flag is not
        proof of complete message history, a verified shared conversation, or permission
        for Lola to send reminders. No communication actions are enabled here.
      </p>
    </section>
  );
}
