import { useCallback, useEffect, useState } from 'react';
import { api, type ExternalReferenceKind, type InternalPricePostingCase,
  type PriceBookSourceItem, type PricePostingRuleProgram,
  type HumanExternalReference } from '../../lib/api';
import { useAuth } from '../../hooks/useAuth';

export function PricePostingPreparationLedger({ programs }: { programs: PricePostingRuleProgram[] }) {
  const { role, hasPermission } = useAuth();
  const canView = hasPermission('view_pricing');
  const canRecord = role === 'owner' || role === 'manager';
  const [cases, setCases] = useState<InternalPricePostingCase[]>([]);
  const [prices, setPrices] = useState<PriceBookSourceItem[]>([]);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedRule, setSelectedRule] = useState('');
  const [selectedPriceIds, setSelectedPriceIds] = useState<string[]>([]);
  const [effectiveDate, setEffectiveDate] = useState('');
  const [acknowledged, setAcknowledged] = useState(false);
  const [writing, setWriting] = useState(false);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [caseId, setCaseId] = useState('');
  const [referenceKind, setReferenceKind] = useState<ExternalReferenceKind>('external_document_reference');
  const [referenceText, setReferenceText] = useState('');
  const [referenceNote, setReferenceNote] = useState('');
  const [referenceAck, setReferenceAck] = useState(false);
  const [references, setReferences] = useState<HumanExternalReference[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setSourceError(null);
    try {
      const [prepared, pricebook] = await Promise.all([
        api.pricePreparations.list(), api.pricePosting.listSourcePrices(),
      ]);
      if (prepared.submission_permitted !== false ||
          prepared.agency_confirmation !== false ||
          !Array.isArray(prepared.items) ||
          prepared.items.some((item) => item.state !== 'internal_preparation_only') ||
          pricebook.status !== 'analysis_only' ||
          pricebook.price_changes_allowed !== false ||
          !Array.isArray(pricebook.items)) {
        throw new Error('Internal source could not be validated');
      }
      setCases(prepared.items);
      setPrices(pricebook.items);
    } catch (error) {
      setCases([]);
      setPrices([]);
      setSourceError(error instanceof Error ? error.message : 'Preparation register unavailable');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (canView) void load(); }, [canView, load]);

  const rule = programs.find((item) => item.id === selectedRule);
  const canPrepare = Boolean(
    canRecord && rule?.human_verified_for_tenant && effectiveDate &&
    selectedPriceIds.length && acknowledged && !writing && !sourceError
  );

  function selectPrice(id: string) {
    setSelectedPriceIds((prev) => prev.includes(id)
      ? prev.filter((value) => value !== id)
      : prev.length < 50 ? [...prev, id] : prev);
    setConfirmation(null);
  }

  async function prepare() {
    if (!canPrepare || !rule) return;
    setWriting(true);
    setSourceError(null);
    setConfirmation(null);
    try {
      const res = await api.pricePreparations.create({
        program_key: rule.program_key, rule_id: rule.id, version: rule.version,
        effective_date: effectiveDate, price_book_ids: selectedPriceIds,
        acknowledged_no_filing_authority: true,
      });
      if (res.state !== 'internal_preparation_only' ||
          res.submission_permitted !== false ||
          res.agency_confirmation !== false ||
          res.price_publication_permitted !== false ||
          !res.audit_event_id || !res.preparation_id) {
        throw new Error('Internal preparation audit was not confirmed');
      }
      setConfirmation(`Internal case ${res.preparation_id} recorded with audit ID ${res.audit_event_id}. No filing.`);
      setSelectedPriceIds([]);
      setAcknowledged(false);
      await load();
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : 'Preparation not recorded');
    } finally {
      setWriting(false);
    }
  }

  async function selectCase(id: string) {
    setCaseId(id);
    setReferences([]);
    setSourceError(null);
    if (!id) return;
    try {
      const res = await api.pricePreparations.listReferences(id);
      if (!Array.isArray(res.items) ||
          res.agency_confirmation !== false ||
          res.submission_permitted !== false ||
          res.items.some((item) => item.provenance_status !== 'human_reported_unverified')) {
        throw new Error('External references were not verified as unconfirmed');
      }
      setReferences(res.items);
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : 'Reference list unavailable');
    }
  }

  async function recordReference() {
    if (!canRecord || !caseId || !referenceAck ||
        referenceText.trim().length < 10 || referenceNote.trim().length < 30 ||
        writing) return;
    setWriting(true);
    setSourceError(null);
    setConfirmation(null);
    try {
      const res = await api.pricePreparations.addReference(caseId, {
        kind: referenceKind, external_reference: referenceText.trim(),
        claimed_event_date: null, note: referenceNote.trim(),
        acknowledged_unverified: true,
      });
      if (res.provenance_status !== 'human_reported_unverified' ||
          res.agency_confirmation !== false ||
          res.submission_permitted !== false ||
          !res.reference_id || !res.audit_event_id) {
        throw new Error('Unverified external reference audit receipt missing');
      }
      setConfirmation(`Unverified reference ${res.reference_id} recorded. This is NOT agency confirmation.`);
      setReferenceText('');
      setReferenceNote('');
      setReferenceAck(false);
      await selectCase(caseId);
    } catch (error) {
      setSourceError(error instanceof Error ? error.message : 'Reference not recorded');
    } finally {
      setWriting(false);
    }
  }

  if (!canView) return (
    <p role="note" className="text-sm text-slate-600">
      Internal price preparation records require Pricing read authorization.
    </p>
  );

  return (
    <section aria-label="Internal price preparation and unverified references"
      className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div>
        <h3 className="font-semibold text-slate-900">Internal preparation register</h3>
        <p className="mt-1 text-sm text-slate-600">
          Freeze stored price-book revisions for internal review and retain human-reported
          external document references. These are not filing approvals or verified receipts.
        </p>
      </div>
      <p role="note" className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
        Government submission and price publication are disabled.
        A manually entered document reference does not prove submission or acceptance.
      </p>
      {sourceError && (
        <p role="alert" className="rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          {sourceError}. Do not infer compliance or filing completion.
          <button type="button" onClick={() => void load()} className="ml-2 underline">Retry register</button>
        </p>
      )}
      {confirmation && <p role="status" className="text-sm text-slate-700">{confirmation}</p>}
      {loading && <p role="status" className="text-sm text-slate-600">Loading internal evidence…</p>}
      {!loading && !sourceError && (
        <div className="space-y-3">
          <h4 className="text-sm font-semibold">Recorded internal cases ({cases.length})</h4>
          {cases.length === 0 && <p className="text-sm text-slate-600">No internal preparations returned; no filing determination made.</p>}
          {cases.map((item) => (
            <article key={item.id} className="rounded-md border border-slate-200 bg-white p-3 text-xs">
              <p className="font-semibold text-slate-800">Case {item.id}</p>
              <p className="text-slate-600">
                Rule version {item.rule_version} · Effective {item.effective_date} ·
                {item.price_book_snapshot.length} source revisions
              </p>
              <p className="text-amber-800">Internal preparation only — never submitted or confirmed</p>
              <button type="button" className="mt-2 underline" onClick={() => void selectCase(item.id)}>
                View human-reported references
              </button>
            </article>
          ))}
        </div>
      )}

      {canRecord && !loading && !sourceError && (
        <div className="space-y-3 border-t border-slate-200 pt-4">
          <h4 className="text-sm font-semibold">Prepare a frozen internal case</h4>
          <label className="block text-sm">
            Human-reviewed program
            <select value={selectedRule} className="mt-1 w-full rounded-md border border-slate-300 p-2"
              onChange={(e) => setSelectedRule(e.target.value)}>
              <option value="">Select a reviewed source version</option>
              {programs.filter((item) => item.human_verified_for_tenant).map((item) => (
                <option key={item.id} value={item.id}>{item.region_code} · {item.program_name} · v{item.version}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm">
            Internal effective date
            <input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)}
              className="mt-1 block rounded-md border border-slate-300 p-2" />
          </label>
          <fieldset className="max-h-48 overflow-y-auto rounded-md bg-white p-3">
            <legend className="text-sm font-medium">Stored price-book revisions (max 50)</legend>
            {prices.map((item) => (
              <label key={item.id} className="flex items-center gap-2 py-1 text-xs">
                <input type="checkbox" checked={selectedPriceIds.includes(item.id)}
                  disabled={selectedPriceIds.length >= 50 && !selectedPriceIds.includes(item.id)}
                  onChange={() => selectPrice(item.id)} />
                {item.sku} · {item.market_code} · Revision {item.revision} · {item.status}
              </label>
            ))}
          </fieldset>
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)} />
            I understand this is an internal evidence snapshot, not authorization to submit or publish.
          </label>
          <button type="button" disabled={!canPrepare} onClick={() => void prepare()}
            className="rounded-md bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-40">
            {writing ? 'Recording…' : 'Create internal case — no filing'}
          </button>
        </div>
      )}

      {caseId && (
        <div className="space-y-2 border-t border-slate-200 pt-4">
          <h4 className="text-sm font-semibold">External references for case {caseId}</h4>
          {references.length === 0 && !sourceError && (
            <p className="text-xs text-slate-600">No human-reported references returned.</p>
          )}
          {references.map((item) => (
            <p key={item.id} className="text-xs text-slate-700">
              {item.reference_kind}: {item.external_reference} — human-reported, not independently verified
            </p>
          ))}
          {canRecord && (
            <>
              <label className="block text-sm">
                Reference category
                <select value={referenceKind} onChange={(e) => setReferenceKind(e.target.value as ExternalReferenceKind)}
                  className="mt-1 block rounded-md border border-slate-300 p-2">
                  <option value="external_document_reference">External document reference</option>
                  <option value="agency_message_reference">Claimed agency message reference</option>
                  <option value="rejection_reference">Rejection document reference</option>
                </select>
              </label>
              <label className="block text-sm">
                Document URL or retained reference (unverified)
                <input value={referenceText} onChange={(e) => setReferenceText(e.target.value)}
                  maxLength={1500} className="mt-1 block w-full rounded-md border border-slate-300 p-2" />
              </label>
              <label className="block text-sm">
                Notes and limitations
                <textarea value={referenceNote} onChange={(e) => setReferenceNote(e.target.value)}
                  maxLength={4000} rows={3} className="mt-1 block w-full rounded-md border border-slate-300 p-2" />
              </label>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" checked={referenceAck}
                  onChange={(e) => setReferenceAck(e.target.checked)} />
                I confirm this is human-reported evidence only, not an authenticated agency receipt.
              </label>
              <button type="button" disabled={!referenceAck || referenceText.trim().length < 10 ||
                  referenceNote.trim().length < 30 || writing}
                onClick={() => void recordReference()}
                className="rounded-md bg-slate-900 px-3 py-2 text-sm text-white disabled:opacity-40">
                Record unverified reference
              </button>
            </>
          )}
        </div>
      )}
    </section>
  );
}
