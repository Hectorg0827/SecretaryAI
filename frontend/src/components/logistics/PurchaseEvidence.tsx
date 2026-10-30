import React, { useEffect, useId, useState } from 'react';
import { api } from '../../lib/api';
import type { Role } from '../../hooks/useAuth';
import {
  APPROVER_ROLES, BUTTON_PRIMARY, BUTTON_SECONDARY, CARD, ConfirmPanel, ErrorNote, Field, INPUT,
  PURCHASING_ROLES, ServerNote, WarningNote, formatDateTime, roleAllowed, useConfirmedWrite,
} from './logisticsShared';

type EvidenceClaim = {
  id: string;
  evidence_key: string;
  document_type: string;
  claimed_sha256?: string | null;
  claimed_amount: string | number | null;
  currency: string | null;
  verification_status: string;
  submitted_by: string;
  created_at: string;
};
type EvidenceList = {
  evidence_claims: EvidenceClaim[];
  claim_count: number;
  unverified_claim_totals_by_currency: Record<string, string>;
  evidence_status_message: string;
};
type IntegrityStatus = { integrity_status: string; malware_scan_status: string };
type MalwareStatus = { scan_status: string; approved_for_review: boolean };
type EvidenceStatus = { integrity: IntegrityStatus; malware: MalwareStatus };
type ScanResult = { scan_id: string; scan_status: string; replayed: boolean };
type ReviewResult = { review_id: string; approved_for_review: boolean; replayed: boolean };
type ClaimResult = { evidence_id: string; replayed: boolean; verification_status: string };
type UploadResult = {
  integrity_check_id?: string;
  replayed?: boolean;
  integrity_status?: string;
  malware_scan_status?: string;
  bytes_received?: number;
};
type ClaimBody = {
  evidence_key: string;
  document_type: string;
  storage_object_key: string;
  claimed_sha256: string;
  claimed_amount?: string;
  currency?: string;
};

export const DOCUMENT_TYPES: Record<string, string> = {
  purchase_invoice: 'Supplier invoice',
  packing_list: 'Packing list',
  bill_of_lading: 'Bill of lading',
  receiving_photo: 'Receiving photo',
  damage_report: 'Damage report',
  freight_invoice: 'Freight invoice',
  customs_entry: 'Customs entry',
  insurance_invoice: 'Insurance invoice',
  broker_invoice: 'Customs broker invoice',
  drayage_invoice: 'Drayage invoice',
  other: 'Other document',
};

const EXTENSIONS: Record<string, string> = { 'application/pdf': 'pdf', 'image/png': 'png', 'image/jpeg': 'jpg' };
const MAX_BYTES = 8 * 1024 * 1024;

export async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

function pathPart(value: string): string {
  return value.replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 80) || 'document';
}

export function evidenceStorageKey(companyId: string, poNumber: string, evidenceKey: string,
  digest: string, extension: string): string {
  return `${companyId}/purchasing/${pathPart(poNumber)}/${pathPart(evidenceKey)}-${digest.slice(0, 12)}.${extension}`;
}

function fileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} bytes`;
}

/** Check a chosen file before upload; returns a plain-language problem or ''. */
async function uploadProblem(file: File | null, expectedSha256: string | null | undefined): Promise<string> {
  if (!file || !EXTENSIONS[file.type]) return 'Choose a PDF, PNG or JPEG file.';
  if (file.size === 0 || file.size > MAX_BYTES) return 'The file must be between 1 byte and 8 MB.';
  if (!expectedSha256) return '';
  let digest: string;
  try {
    digest = await sha256Hex(file);
  } catch {
    return 'This browser could not read the file.';
  }
  return digest === expectedSha256 ? ''
    : 'This is not the same file whose details were recorded (its fingerprint is different). Choose the original file.';
}

function scanLabel(status: string): string {
  return status === 'clean' ? 'No virus found' : status === 'infected' ? 'Virus found — do not open' :
    status === 'not_scanned' ? 'Not scanned yet' : status;
}

function EvidenceRow({ claim, role, currentUser }: { claim: EvidenceClaim; role: Role; currentUser: string | null }) {
  const [status, setStatus] = useState<EvidenceStatus | null>(null);
  const [statusError, setStatusError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState('');
  const scan = useConfirmedWrite<ScanResult>();
  const review = useConfirmedWrite<ReviewResult>();
  const upload = useConfirmedWrite<UploadResult>();
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadIssue, setUploadIssue] = useState('');
  const fileInputId = useId();
  const base = `/api/logistics/evidence/${encodeURIComponent(claim.id)}`;

  async function loadStatus() {
    setLoading(true);
    setStatusError(null);
    try {
      const [integrity, malware] = await Promise.all([
        api.get<IntegrityStatus>(`${base}/integrity`),
        api.get<MalwareStatus>(`${base}/malware-status`),
      ]);
      if (typeof integrity?.integrity_status !== 'string' || typeof malware?.scan_status !== 'string') {
        throw new Error('Document status is unavailable.');
      }
      setStatus({ integrity, malware });
    } catch (reason) {
      setStatus(null);
      setStatusError(reason);
    } finally {
      setLoading(false);
    }
  }

  async function confirmScan() {
    const result = await scan.send(() => api.post<ScanResult>(`${base}/scan`, {}));
    if (result) {
      setNotice(`Virus scan saved by the server: ${scanLabel(result.scan_status)}${result.replayed ? ' (already recorded earlier)' : ''}.`);
      await loadStatus();
    }
  }

  async function reviewUpload() {
    const problem = await uploadProblem(uploadFile, claim.claimed_sha256);
    setUploadIssue(problem);
    if (!problem) upload.open();
  }

  async function confirmUpload() {
    if (!uploadFile) return;
    const form = new FormData();
    form.append('file', uploadFile, uploadFile.name);
    const result = await upload.send(() => api.upload<UploadResult>(`${base}/upload`, form));
    if (result) {
      setUploadFile(null);
      setNotice(result.integrity_status === 'sha256_matched_at_upload'
        ? `The server stored the file${typeof result.bytes_received === 'number' ? ` (${fileSize(result.bytes_received)})` : ''}`
          + ` and confirmed it matches the recorded fingerprint${result.replayed ? ' (already recorded earlier)' : ''}.`
          + ' It has not been scanned for viruses yet.'
        : 'The server did not confirm the upload. Check the status before trying again.');
      await loadStatus();
    }
  }

  async function confirmReview() {
    const result = await review.send(() => api.post<ReviewResult>(`${base}/approve-review`, {}));
    if (result) {
      setNotice(result.approved_for_review
        ? `The server recorded your review${result.replayed ? ' (already recorded earlier)' : ''}.`
        : 'The server did not confirm the review.');
      await loadStatus();
    }
  }

  const uploaded = status?.integrity.integrity_status === 'sha256_matched_at_upload';
  const canUpload = roleAllowed(role, PURCHASING_ROLES) && status?.integrity.integrity_status === 'not_checked'
    && claim.verification_status === 'unverified';
  const scanStatus = status?.malware.scan_status;
  const canScan = roleAllowed(role, PURCHASING_ROLES) && uploaded && scanStatus === 'not_scanned';
  const ownSubmission = Boolean(currentUser) && currentUser === claim.submitted_by;
  const canReview = roleAllowed(role, APPROVER_ROLES) && scanStatus === 'clean'
    && status?.malware.approved_for_review === false && !ownSubmission;

  return <li className="border-t border-slate-200 py-3 space-y-2">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="text-sm">
        <p className="font-medium text-slate-900">{DOCUMENT_TYPES[claim.document_type] ?? claim.document_type} · {claim.evidence_key}</p>
        <p className="text-slate-600">
          {claim.claimed_amount != null ? `Amount stated (not checked): ${claim.claimed_amount} ${claim.currency ?? ''} · ` : ''}
          Added {formatDateTime(claim.created_at)}
        </p>
      </div>
      <button type="button" className={BUTTON_SECONDARY} disabled={loading} onClick={loadStatus}>
        {loading ? 'Checking…' : status ? 'Refresh status' : 'Check status'}
      </button>
    </div>
    {Boolean(statusError) && <ErrorNote error={statusError} fallback="Unable to load the document status." />}
    {status && <dl className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-sm">
      <div><dt className="text-slate-600">File received</dt><dd>{uploaded ? 'Yes, fingerprint matches' : 'Not uploaded yet'}</dd></div>
      <div><dt className="text-slate-600">Virus scan</dt><dd>{scanLabel(status.malware.scan_status)}</dd></div>
      <div><dt className="text-slate-600">Independent review</dt><dd>{status.malware.approved_for_review ? 'Reviewed' : 'Not reviewed'}</dd></div>
    </dl>}
    {status && !uploaded && <p className="text-sm text-slate-600">The file itself has not been uploaded yet.</p>}
    {canUpload && !upload.confirming && <form className="flex flex-wrap items-end gap-2" aria-label="Upload the file"
      onSubmit={(event) => { event.preventDefault(); void reviewUpload(); }}>
      <Field label="File to upload (the same PDF, PNG or JPEG)" htmlFor={fileInputId}>
        <input id={fileInputId} type="file" accept="application/pdf,image/png,image/jpeg" className="text-sm"
          onChange={(event) => { setUploadFile(event.target.files?.[0] ?? null); setUploadIssue(''); }} />
      </Field>
      <button type="submit" className={BUTTON_PRIMARY}>Review upload</button>
    </form>}
    {uploadIssue && <p role="alert" className="text-sm text-red-700">{uploadIssue}</p>}
    {upload.confirming && uploadFile && <ConfirmPanel title="Upload this file?" confirmLabel="Upload file"
      busy={upload.busy} onConfirm={confirmUpload} onCancel={upload.cancel}>
      <p>
        <strong>{uploadFile.name}</strong> ({fileSize(uploadFile.size)}) is sent to your company’s private document
        storage and checked against the fingerprint recorded for <strong>{claim.evidence_key}</strong>.
      </p>
      <p>Once stored it cannot be replaced or deleted from here. This step does not scan it for viruses, approve any
        payment, change costs or stock, or write to QuickBooks.</p>
    </ConfirmPanel>}
    {Boolean(upload.error) && <ErrorNote error={upload.error} fallback="The file was not uploaded." />}
    {scanStatus === 'infected' && <WarningNote>The virus scanner flagged this file. Do not open or use it.</WarningNote>}
    {status && scanStatus === 'clean' && !status.malware.approved_for_review && ownSubmission
      && roleAllowed(role, APPROVER_ROLES) && <p className="text-sm text-slate-700">
      You added this document, so a different owner or manager must review it.
    </p>}
    {canScan && !scan.confirming && <button type="button" className={BUTTON_PRIMARY} onClick={scan.open}>Run virus scan</button>}
    {scan.confirming && <ConfirmPanel title="Scan this document for viruses?" confirmLabel="Run scan"
      busy={scan.busy} onConfirm={confirmScan} onCancel={scan.cancel}>
      <p>The stored file for <strong>{claim.evidence_key}</strong> is checked by your company’s own virus scanner.</p>
      <p>The result (no virus found, or virus found) is saved permanently and cannot be changed. The file is not
        opened, shared or sent outside SecretaryAI, and nothing is approved for payment or written to QuickBooks.</p>
    </ConfirmPanel>}
    {canReview && !review.confirming && <button type="button" className={BUTTON_PRIMARY} onClick={review.open}>Mark as reviewed</button>}
    {review.confirming && <ConfirmPanel title="Record your review of this document?" confirmLabel="Mark as reviewed"
      busy={review.busy} onConfirm={confirmReview} onCancel={review.cancel}>
      <p>This records under your name that you independently reviewed <strong>{claim.evidence_key}</strong>,
        which passed the virus scan. You cannot be the person who added or scanned it.</p>
      <p>It does not approve any payment, does not change costs or stock, and is not written to QuickBooks.
        It cannot be undone.</p>
    </ConfirmPanel>}
    {Boolean(scan.error) && <ErrorNote error={scan.error} fallback="The virus scan was not completed." />}
    {Boolean(review.error) && <ErrorNote error={review.error} fallback="The review was not recorded." />}
    {notice && <ServerNote>{notice}</ServerNote>}
  </li>;
}

function NewEvidenceForm({ poNumber, companyId, onRecorded }: {
  poNumber: string; companyId: string; onRecorded: (result: ClaimResult) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [documentType, setDocumentType] = useState('');
  const [reference, setReference] = useState('');
  const [amount, setAmount] = useState('');
  const [currency, setCurrency] = useState('USD');
  const [problem, setProblem] = useState('');
  const [pending, setPending] = useState<ClaimBody | null>(null);
  const write = useConfirmedWrite<ClaimResult>();
  const idBase = useId();

  async function review() {
    setPending(null);
    const key = reference.trim();
    const extension = file ? EXTENSIONS[file.type] : undefined;
    if (!file || !extension) { setProblem('Choose a PDF, PNG or JPEG file.'); return; }
    if (file.size === 0 || file.size > MAX_BYTES) { setProblem('The file must be between 1 byte and 8 MB.'); return; }
    if (!DOCUMENT_TYPES[documentType]) { setProblem('Choose the document type.'); return; }
    if (!key || key.length > 150) { setProblem('Enter a document reference (up to 150 characters).'); return; }
    const amountText = amount.trim();
    const currencyText = currency.trim().toUpperCase();
    if (amountText && !/^\d{1,16}(\.\d{1,2})?$/.test(amountText)) { setProblem('Enter the amount as a number with up to 2 decimals.'); return; }
    if (amountText && !/^[A-Z]{3}$/.test(currencyText)) { setProblem('Enter a 3-letter currency code such as USD.'); return; }
    let digest: string;
    try {
      digest = await sha256Hex(file);
    } catch {
      setProblem('This browser could not read the file.');
      return;
    }
    setProblem('');
    setPending({
      evidence_key: key,
      document_type: documentType,
      storage_object_key: evidenceStorageKey(companyId, poNumber, key, digest, extension),
      claimed_sha256: digest,
      ...(amountText ? { claimed_amount: amountText, currency: currencyText } : {}),
    });
    write.open();
  }

  async function confirm() {
    if (!pending) return;
    const result = await write.send(() => api.post<ClaimResult>(
      `/api/logistics/orders/${encodeURIComponent(poNumber)}/evidence`, pending));
    if (result) onRecorded(result);
  }

  return <form className={CARD} aria-label="Add a document"
    onSubmit={(event) => { event.preventDefault(); void review(); }}>
    <h5 className="font-medium text-slate-900">Add a document</h5>
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
      <Field label="File (PDF, PNG or JPEG, up to 8 MB)" htmlFor={`${idBase}-file`}>
        <input id={`${idBase}-file`} type="file" accept="application/pdf,image/png,image/jpeg"
          className="text-sm" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
      </Field>
      <Field label="Document type" htmlFor={`${idBase}-type`}>
        <select id={`${idBase}-type`} className={INPUT} value={documentType}
          onChange={(event) => setDocumentType(event.target.value)}>
          <option value="">Choose a type</option>
          {Object.entries(DOCUMENT_TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </Field>
      <Field label="Document reference" htmlFor={`${idBase}-ref`} help="For example the invoice number.">
        <input id={`${idBase}-ref`} className={INPUT} value={reference} maxLength={150}
          onChange={(event) => setReference(event.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Amount on document (optional)" htmlFor={`${idBase}-amount`}>
          <input id={`${idBase}-amount`} className={INPUT} inputMode="decimal" value={amount}
            onChange={(event) => setAmount(event.target.value)} />
        </Field>
        <Field label="Currency" htmlFor={`${idBase}-currency`}>
          <input id={`${idBase}-currency`} className={INPUT} maxLength={3} value={currency}
            onChange={(event) => setCurrency(event.target.value)} />
        </Field>
      </div>
    </div>
    {problem && <p role="alert" className="text-sm text-red-700">{problem}</p>}
    {!write.confirming && <button type="submit" className={BUTTON_PRIMARY}>Review document details</button>}
    {write.confirming && pending && <ConfirmPanel title="Record these document details?" confirmLabel="Record details"
      busy={write.busy} onConfirm={confirm} onCancel={write.cancel}>
      <p>
        {DOCUMENT_TYPES[pending.document_type]} <strong>{pending.evidence_key}</strong> for purchase order
        {' '}<strong>{poNumber}</strong>{pending.claimed_amount ? `, stated amount ${pending.claimed_amount} ${pending.currency}` : ''}.
      </p>
      <p>
        This saves the document’s description and digital fingerprint as an unchecked claim. It cannot be edited
        or deleted afterwards. The file itself is not uploaded by this step, no payment is approved, costs and stock
        do not change, and nothing is written to QuickBooks.
      </p>
    </ConfirmPanel>}
    {Boolean(write.error) && <ErrorNote error={write.error} fallback="The document details were not recorded." />}
  </form>;
}

/** Documents attached to a purchase order and their upload, virus-scan and review status. */
export function PurchaseEvidence({ poNumber, poStatus, role, currentUser, companyId }: {
  poNumber: string; poStatus: string; role: Role; currentUser: string | null; companyId: string | null;
}) {
  const [data, setData] = useState<EvidenceList | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [revision, setRevision] = useState(0);
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let current = true;
    setData(null);
    setError(null);
    api.get<EvidenceList>(`/api/logistics/orders/${encodeURIComponent(poNumber)}/evidence`).then((result) => {
      if (!Array.isArray(result?.evidence_claims)) throw new Error('Purchase documents are unavailable.');
      if (current) setData(result);
    }).catch((reason: unknown) => { if (current) setError(reason); });
    return () => { current = false; };
  }, [poNumber, revision]);

  const canAdd = roleAllowed(role, PURCHASING_ROLES) && poStatus !== 'draft' && Boolean(companyId);

  return <section aria-label="Purchase documents" className="space-y-2">
    <h4 className="font-medium">Documents</h4>
    {error ? <ErrorNote error={error} fallback="Unable to load purchase documents." /> :
      data === null ? <p role="status" className="text-sm">Loading documents…</p> :
      <>
        {data.evidence_claims.length === 0 ? <p className="text-sm text-slate-600">No documents recorded for this order.</p> :
          <ul>{data.evidence_claims.map((claim) =>
            <EvidenceRow key={claim.id} claim={claim} role={role} currentUser={currentUser} />)}</ul>}
        <p className="text-xs text-slate-600">
          Amounts and files are as submitted and have not been checked against invoices or QuickBooks.
        </p>
      </>}
    {notice && <ServerNote>{notice}</ServerNote>}
    {canAdd && !adding && <button type="button" className={BUTTON_SECONDARY}
      onClick={() => { setNotice(''); setAdding(true); }}>Add a document</button>}
    {poStatus === 'draft' && roleAllowed(role, PURCHASING_ROLES) &&
      <p className="text-xs text-slate-600">Documents can be added once the order is approved.</p>}
    {canAdd && adding && companyId && <NewEvidenceForm poNumber={poNumber} companyId={companyId} onRecorded={(result) => {
      setAdding(false);
      setNotice(result.replayed
        ? 'These document details were already recorded earlier; no duplicate was created.'
        : 'The server recorded the document details as an unchecked claim. Next, check its status below and upload the file itself.');
      setRevision((value) => value + 1);
    }} />}
  </section>;
}
