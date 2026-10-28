import React, { act } from 'react';
import { createHash } from 'node:crypto';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../lib/api';
import { PurchaseEvidence, evidenceStorageKey } from './PurchaseEvidence';
import {
  button, cleanup, click, confirm, dialog, field, flush, mockApi, render, setRole, type,
} from './logisticsTestHarness';
import type { Role } from '../../hooks/useAuth';

const EVIDENCE_ID = '7c9e6679-7425-40de-944b-e07fc1f90ae7';
const LIST_PATH = '/api/logistics/orders/PO-1/evidence';
const BASE = `/api/logistics/evidence/${EVIDENCE_ID}`;
const FILE_BYTES = new TextEncoder().encode('%PDF-1.4 synthetic invoice');
const FILE_SHA256 = createHash('sha256').update(FILE_BYTES).digest('hex');

function claims(submittedBy = 'clerk-1') {
  return {
    evidence_claims: [{
      id: EVIDENCE_ID, evidence_key: 'INV-42', document_type: 'purchase_invoice', claimed_amount: '120.50',
      claimed_sha256: FILE_SHA256,
      currency: 'USD', verification_status: 'unverified', submitted_by: submittedBy, created_at: '2026-10-20T09:00:00Z',
    }],
    claim_count: 1, unverified_claim_totals_by_currency: { USD: '120.50' }, evidence_status_message: '',
  };
}

function status(scan: string, reviewed = false, uploaded = true) {
  return {
    [`${BASE}/integrity`]: { integrity_status: uploaded ? 'sha256_matched_at_upload' : 'not_checked', malware_scan_status: scan },
    [`${BASE}/malware-status`]: { scan_status: scan, approved_for_review: reviewed },
  };
}

async function choose(container: HTMLElement, file: File) {
  const input = field(container, 'File to upload (the same PDF, PNG or JPEG)') as HTMLInputElement;
  await act(async () => {
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });
}

async function show(role: Role, user: string, poStatus = 'approved') {
  return render(<PurchaseEvidence poNumber="PO-1" poStatus={poStatus} role={role} currentUser={user} companyId="company-1" />);
}

afterEach(cleanup);

describe('purchase documents', () => {
  it('lists documents and their status without claiming they were checked', async () => {
    setRole('owner');
    mockApi({ [LIST_PATH]: claims(), ...status('not_scanned', false, false) });
    const container = await show('owner', 'approver-1');
    expect(container.textContent).toContain('Supplier invoice · INV-42');
    expect(container.textContent).toContain('Amount stated (not checked): 120.50 USD');
    await click(button(container, 'Check status'));
    expect(container.textContent).toContain('Not uploaded yet');
    expect(container.textContent).toContain('The file itself has not been uploaded yet.');
    expect(button(container, 'Run virus scan')).toBeUndefined();
  });

  it('confirms before a virus scan and shows the scanner result from the server', async () => {
    const { post, getRoutes } = mockApi({ [LIST_PATH]: claims(), ...status('not_scanned') },
      { [`${BASE}/scan`]: { scan_id: 's1', scan_status: 'clean', replayed: false } });
    const container = await show('back_office', 'clerk-2');
    await click(button(container, 'Check status'));
    await click(button(container, 'Run virus scan'));
    expect(post).not.toHaveBeenCalled();
    expect(dialog(container)?.textContent).toContain('saved permanently');
    Object.assign(getRoutes, status('clean'));
    await confirm(container);
    expect(post).toHaveBeenCalledWith(`${BASE}/scan`, {});
    expect(container.querySelector('[role="status"]')?.textContent).toContain('Virus scan saved by the server: No virus found');
    // back office can never record the independent review
    expect(button(container, 'Mark as reviewed')).toBeUndefined();
  });

  it('shows a scanner outage clearly', async () => {
    mockApi({ [LIST_PATH]: claims(), ...status('not_scanned') },
      { [`${BASE}/scan`]: new Error('Malware scanner unavailable') });
    const container = await show('owner', 'approver-1');
    await click(button(container, 'Check status'));
    await click(button(container, 'Run virus scan'));
    await confirm(container);
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Malware scanner unavailable');
    expect(alert?.textContent).toContain('Refresh to check whether anything changed');
  });

  it('lets an independent owner record the review after confirmation', async () => {
    const { post } = mockApi({ [LIST_PATH]: claims(), ...status('clean') },
      { [`${BASE}/approve-review`]: { review_id: 'rv1', approved_for_review: true, replayed: false } });
    const container = await show('owner', 'approver-1');
    await click(button(container, 'Check status'));
    await click(button(container, 'Mark as reviewed'));
    expect(post).not.toHaveBeenCalled();
    expect(dialog(container)?.textContent).toContain('does not approve any payment');
    await confirm(container);
    expect(post).toHaveBeenCalledWith(`${BASE}/approve-review`, {});
    expect(container.textContent).toContain('The server recorded your review');
  });

  it('hides the review from the person who added the document', async () => {
    mockApi({ [LIST_PATH]: claims('approver-1'), ...status('clean') });
    const container = await show('manager', 'approver-1');
    await click(button(container, 'Check status'));
    expect(button(container, 'Mark as reviewed')).toBeUndefined();
    expect(container.textContent).toContain('You added this document');
  });

  it('records document details with the file fingerprint after confirmation', async () => {
    const bytes = new TextEncoder().encode('%PDF-1.4 test invoice');
    const digest = createHash('sha256').update(bytes).digest('hex');
    const { post } = mockApi({ [LIST_PATH]: { ...claims(), evidence_claims: [] } },
      { [LIST_PATH]: { evidence_id: 'e1', replayed: false, verification_status: 'unverified' } });
    const container = await show('back_office', 'clerk-1');
    await click(button(container, 'Add a document'));
    const input = field(container, 'File (PDF, PNG or JPEG, up to 8 MB)') as HTMLInputElement;
    const file = new File([bytes], 'invoice.pdf', { type: 'application/pdf' });
    await act(async () => {
      Object.defineProperty(input, 'files', { value: [file], configurable: true });
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await type(field(container, 'Document type'), 'purchase_invoice');
    await type(field(container, 'Document reference'), 'INV 42/A');
    await type(field(container, 'Amount on document (optional)'), '120.50');
    await click(button(container, 'Review document details'));
    await flush();
    expect(post).not.toHaveBeenCalled();
    expect(dialog(container)?.textContent).toContain('cannot be edited or deleted afterwards');
    await confirm(container);
    expect(post).toHaveBeenCalledWith(LIST_PATH, {
      evidence_key: 'INV 42/A',
      document_type: 'purchase_invoice',
      storage_object_key: `company-1/purchasing/PO-1/INV-42-A-${digest.slice(0, 12)}.pdf`,
      claimed_sha256: digest,
      claimed_amount: '120.50',
      currency: 'USD',
    });
    expect(container.textContent).toContain('The server recorded the document details as an unchecked claim.');
  });

  it('uploads the file only after confirmation and shows the server result', async () => {
    const { getRoutes } = mockApi({ [LIST_PATH]: claims(), ...status('not_scanned', false, false) });
    const upload = vi.spyOn(api, 'upload').mockResolvedValue({
      integrity_check_id: 'ic-1', replayed: false, integrity_status: 'sha256_matched_at_upload',
      malware_scan_status: 'not_scanned', bytes_received: FILE_BYTES.length,
    });
    const container = await show('back_office', 'clerk-2');
    await click(button(container, 'Check status'));
    await choose(container, new File([FILE_BYTES], 'invoice.pdf', { type: 'application/pdf' }));
    await click(button(container, 'Review upload'));
    await flush();
    expect(upload).not.toHaveBeenCalled();
    expect(dialog(container)?.textContent).toContain('cannot be replaced or deleted');
    expect(dialog(container)?.textContent).toContain('does not scan it for viruses');
    Object.assign(getRoutes, status('not_scanned'));
    await confirm(container);
    expect(upload).toHaveBeenCalledTimes(1);
    const [path, form] = upload.mock.calls[0];
    expect(path).toBe(`${BASE}/upload`);
    expect(form).toBeInstanceOf(FormData);
    const sent = (form as FormData).get('file') as File;
    expect(sent.name).toBe('invoice.pdf');
    expect(new Uint8Array(await sent.arrayBuffer())).toEqual(FILE_BYTES);
    expect(container.querySelector('[role="status"]')?.textContent).toContain(
      `The server stored the file (${FILE_BYTES.length} bytes) and confirmed it matches the recorded fingerprint.`);
    expect(container.textContent).toContain('It has not been scanned for viruses yet.');
    expect(container.textContent).toContain('Yes, fingerprint matches');
    expect(button(container, 'Review upload')).toBeUndefined();
  });

  it('refuses a different file before anything is sent', async () => {
    mockApi({ [LIST_PATH]: claims(), ...status('not_scanned', false, false) });
    const upload = vi.spyOn(api, 'upload');
    const container = await show('owner', 'approver-1');
    await click(button(container, 'Check status'));
    await choose(container, new File([new TextEncoder().encode('%PDF-1.4 other')], 'other.pdf', { type: 'application/pdf' }));
    await click(button(container, 'Review upload'));
    await flush();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('not the same file');
    expect(dialog(container)).toBeNull();
    expect(upload).not.toHaveBeenCalled();
    await choose(container, new File(['plain text'], 'notes.txt', { type: 'text/plain' }));
    await click(button(container, 'Review upload'));
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Choose a PDF, PNG or JPEG file.');
    expect(upload).not.toHaveBeenCalled();
  });

  it('shows a refused upload as an error and never as stored', async () => {
    mockApi({ [LIST_PATH]: claims(), ...status('not_scanned', false, false) });
    vi.spyOn(api, 'upload').mockRejectedValue(new Error('Private evidence storage unavailable'));
    const container = await show('manager', 'approver-1');
    await click(button(container, 'Check status'));
    await choose(container, new File([FILE_BYTES], 'invoice.pdf', { type: 'application/pdf' }));
    await click(button(container, 'Review upload'));
    await flush();
    await confirm(container);
    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('Private evidence storage unavailable');
    expect(alert?.textContent).toContain('Refresh to check whether anything changed');
    expect(container.textContent).not.toContain('The server stored the file');
  });

  it('does not offer the upload to sales staff or once the file is stored', async () => {
    mockApi({ [LIST_PATH]: claims(), ...status('not_scanned', false, false) });
    let container = await show('sales_rep', 'rep-1');
    await click(button(container, 'Check status'));
    expect(button(container, 'Review upload')).toBeUndefined();
    await cleanup();
    mockApi({ [LIST_PATH]: claims(), ...status('not_scanned') });
    container = await show('owner', 'approver-1');
    await click(button(container, 'Check status'));
    expect(button(container, 'Review upload')).toBeUndefined();
  });

  it('builds storage keys the server accepts', () => {
    const key = evidenceStorageKey('c1', 'PO 1/../x', 'Invoice #1', 'a'.repeat(64), 'png');
    expect(key).toBe('c1/purchasing/PO-1-x/Invoice-1-aaaaaaaaaaaa.png');
    expect(key).toMatch(/^[A-Za-z0-9_./-]+$/);
    expect(key).not.toContain('..');
  });

  it('does not offer to add documents to a draft order or to sales staff', async () => {
    mockApi({ [LIST_PATH]: claims() });
    let container = await show('owner', 'approver-1', 'draft');
    expect(button(container, 'Add a document')).toBeUndefined();
    await cleanup();
    mockApi({ [LIST_PATH]: claims() });
    container = await show('sales_rep', 'rep-1');
    expect(button(container, 'Add a document')).toBeUndefined();
  });

  it('shows an unavailable document list as an error', async () => {
    mockApi({ [LIST_PATH]: new Error('Purchasing evidence unavailable') });
    const container = await show('owner', 'approver-1');
    expect(container.querySelector('[role="alert"]')?.textContent).toContain('Purchasing evidence unavailable');
    expect(container.textContent).not.toContain('No documents recorded');
  });
});
