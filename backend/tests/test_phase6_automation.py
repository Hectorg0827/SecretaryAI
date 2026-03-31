"""
Phase 6 tests — browser automation service + ingestion pipeline hardening.

Tests for:
  BrowserAutomationService
    - enqueue returns a UUID job_id
    - enqueue is idempotent when same correlation_id is active
    - claim_next_job returns None on empty queue
    - claim_next_job returns and claims the pending row
    - complete_job updates status to completed with confidence
    - fail_job below max_attempts sets next_retry_at
    - fail_job at max_attempts leaves next_retry_at None
    - cancel_job flips pending → cancelled
    - cancel_stale_running_jobs calls update with lt(started_at, cutoff)
    - get_job returns None on DB error (non-fatal)

  IngestionPipeline
    - classify_entity: known and unknown filenames
    - scan_directory: empty / non-existent directory returns []
    - compute_hash: consistent hash for the same content
    - register_file: new file returns (id, True)
    - register_file: duplicate (path+hash) returns (id, False) without insert
    - get_pending: returns pending rows + retry-eligible error rows
    - mark_processing: calls update with status=processing
    - mark_done: sets status=done and rows_ingested
    - mark_error: below max_attempts sets next_retry_at
    - mark_error: at max_attempts leaves next_retry_at None
    - mark_skipped: sets status=skipped

  IngestionScheduler (unit)
    - _process_company: scan→register→process flow tracked correctly
    - _ingest_file: unsupported extension raises _SkipFile
    - _ingest_file: empty parse result raises _SkipFile
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_db() -> MagicMock:
    return MagicMock()


def _ba_service(db=None, company_id="co-1", worker_id="w-1"):
    from app.services.browser_automation import BrowserAutomationService
    return BrowserAutomationService(db or _make_db(), company_id, worker_id)


def _ip(db=None, company_id="co-1"):
    from app.services.ingestion_pipeline import IngestionPipeline
    return IngestionPipeline(db or _make_db(), company_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  BrowserAutomationService
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserAutomationEnqueue:
    def _db_no_existing(self):
        db = _make_db()
        # No active job with same correlation_id
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
        # Insert succeeds
        db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return db

    def test_enqueue_returns_uuid(self):
        db = self._db_no_existing()
        svc = _ba_service(db)
        job_id = svc.enqueue("accounts_receivable")
        assert job_id is not None
        assert len(job_id) == 36

    def test_enqueue_inserts_to_table(self):
        db = self._db_no_existing()
        svc = _ba_service(db)
        svc.enqueue("accounts_receivable", priority=3)
        db.table.assert_any_call("browser_automation_jobs")
        db.table.return_value.insert.assert_called_once()
        inserted = db.table.return_value.insert.call_args[0][0]
        assert inserted["capability"] == "accounts_receivable"
        assert inserted["priority"] == 3
        assert inserted["status"] == "pending"
        assert inserted["company_id"] == "co-1"

    def test_enqueue_idempotent_on_active_correlation_id(self):
        db = _make_db()
        # _find_active_by_correlation:
        # .select("id").eq(company_id).eq(correlation_id).in_(status).limit(1).execute()
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = [
            {"id": "existing-job-id"}
        ]
        svc = _ba_service(db)
        job_id = svc.enqueue("invoices", correlation_id="corr-abc")
        assert job_id == "existing-job-id"
        # insert should NOT be called
        db.table.return_value.insert.assert_not_called()

    def test_enqueue_returns_none_on_db_error(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
        db.table.return_value.insert.return_value.execute.side_effect = RuntimeError("DB down")
        svc = _ba_service(db)
        result = svc.enqueue("invoices")
        assert result is None  # non-fatal

    def test_enqueue_clamps_priority(self):
        db = self._db_no_existing()
        svc = _ba_service(db)
        svc.enqueue("customers", priority=99)
        inserted = db.table.return_value.insert.call_args[0][0]
        assert inserted["priority"] == 10  # clamped to max


class TestBrowserAutomationClaimComplete:
    def _db_with_pending(self, rows, retry_rows=None):
        db = _make_db()
        # pending query
        db.table.return_value.select.return_value.eq.return_value \
            .in_.return_value.order.return_value.order.return_value \
            .limit.return_value.execute.return_value.data = rows
        # retry-eligible query
        db.table.return_value.select.return_value.eq.return_value \
            .eq.return_value.lte.return_value.order.return_value \
            .limit.return_value.execute.return_value.data = retry_rows or []
        # _requeue_timed_out: update chain
        db.table.return_value.update.return_value.eq.return_value.eq.return_value \
            .lt.return_value.execute.return_value = MagicMock()
        return db

    def test_claim_returns_none_on_empty_queue(self):
        db = self._db_with_pending([])
        svc = _ba_service(db)
        result = svc.claim_next_job()
        assert result is None

    def test_claim_flips_pending_to_running(self):
        pending_row = {"id": "job-1", "status": "pending"}
        db = self._db_with_pending([pending_row])
        # _try_claim update
        db.table.return_value.update.return_value.eq.return_value \
            .in_.return_value.execute.return_value.data = [{"id": "job-1", "status": "running"}]
        svc = _ba_service(db)
        result = svc.claim_next_job()
        assert result is not None
        assert result["id"] == "job-1"

    def test_complete_job_updates_status(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        svc = _ba_service(db)
        svc.complete_job("job-1", {"data": "ok"}, 85)
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["status"] == "completed"
        assert update_args["confidence_pct"] == 85
        assert update_args["result"] == {"data": "ok"}

    def test_complete_job_clamps_confidence(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        svc = _ba_service(db)
        svc.complete_job("job-1", {}, 150)  # over 100
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["confidence_pct"] == 100

    def test_complete_job_non_fatal_on_db_error(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.side_effect = \
            RuntimeError("DB down")
        svc = _ba_service(db)
        svc.complete_job("job-1", {}, 80)  # must not raise


class TestBrowserAutomationFailCancel:
    def _db_for_fail(self, attempt=0, max_att=3):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"attempt_count": attempt, "max_attempts": max_att}
        ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        return db

    def test_fail_job_below_max_sets_next_retry(self):
        db = self._db_for_fail(attempt=0, max_att=3)
        svc = _ba_service(db)
        svc.fail_job("job-1", "network error", retry=True)
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["status"] == "failed"
        assert update_args["next_retry_at"] is not None
        assert update_args["attempt_count"] == 1

    def test_fail_job_at_max_leaves_no_retry(self):
        db = self._db_for_fail(attempt=2, max_att=3)
        svc = _ba_service(db)
        svc.fail_job("job-1", "persistent error", retry=True)
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["next_retry_at"] is None

    def test_fail_job_retry_false_skips_retry(self):
        db = self._db_for_fail(attempt=0, max_att=3)
        svc = _ba_service(db)
        svc.fail_job("job-1", "fatal error", retry=False)
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["next_retry_at"] is None

    def test_cancel_job_calls_update_with_cancelled(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value \
            .in_.return_value.execute.return_value = MagicMock()
        svc = _ba_service(db)
        svc.cancel_job("job-1")
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["status"] == "cancelled"

    def test_cancel_stale_calls_lt_on_started_at(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.eq.return_value \
            .lt.return_value.execute.return_value.data = []
        svc = _ba_service(db)
        svc.cancel_stale_running_jobs(timeout_seconds=300)
        # Verify lt() was called (timeout cutoff)
        db.table.return_value.update.return_value.eq.return_value.eq.return_value \
            .lt.assert_called_once()

    def test_get_job_returns_none_on_db_error(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .execute.side_effect = RuntimeError("DB down")
        svc = _ba_service(db)
        result = svc.get_job("job-1")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
#  IngestionPipeline
# ═══════════════════════════════════════════════════════════════════════════════

class TestClassifyEntity:
    def test_customer_filename(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        assert IngestionPipeline.classify_entity("customers_2024.csv") == "customers"

    def test_invoice_filename(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        assert IngestionPipeline.classify_entity("Invoice_Export.xlsx") == "invoices"

    def test_inventory_filename(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        assert IngestionPipeline.classify_entity("inventory_items.csv") == "inventory"

    def test_item_maps_to_inventory(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        assert IngestionPipeline.classify_entity("items_list.csv") == "inventory"

    def test_unknown_filename(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        assert IngestionPipeline.classify_entity("dump_2024.csv") == "unknown"

    def test_vendor_filename(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        assert IngestionPipeline.classify_entity("vendor_master.xlsx") == "vendors"


class TestScanDirectory:
    def test_nonexistent_directory_returns_empty(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        result = IngestionPipeline.scan_directory("/does/not/exist/xyz")
        assert result == []

    def test_empty_directory_returns_empty(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            result = IngestionPipeline.scan_directory(tmpdir)
            assert result == []

    def test_csv_files_found(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "customers.csv").write_text("id,name\n1,Acme")
            Path(tmpdir, "notes.txt").write_text("ignore me")
            result = IngestionPipeline.scan_directory(tmpdir)
            assert len(result) == 1
            assert result[0]["name"] == "customers.csv"

    def test_hidden_files_skipped(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, ".hidden.csv").write_text("id\n1")
            Path(tmpdir, "visible.csv").write_text("id\n1")
            result = IngestionPipeline.scan_directory(tmpdir)
            names = [r["name"] for r in result]
            assert ".hidden.csv" not in names
            assert "visible.csv" in names

    def test_temp_files_skipped(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "~$lockfile.xlsx").write_bytes(b"\x00")
            Path(tmpdir, "real.xlsx").write_bytes(b"\x00")
            result = IngestionPipeline.scan_directory(tmpdir)
            names = [r["name"] for r in result]
            assert "~$lockfile.xlsx" not in names


class TestComputeHash:
    def test_hash_is_consistent(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"id,name\n1,Acme")
            path = f.name
        try:
            h1 = IngestionPipeline.compute_hash(path)
            h2 = IngestionPipeline.compute_hash(path)
            assert h1 == h2
            assert len(h1) == 64  # SHA-256 hex
        finally:
            os.unlink(path)

    def test_different_content_different_hash(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        with tempfile.TemporaryDirectory() as d:
            f1 = Path(d, "a.csv")
            f2 = Path(d, "b.csv")
            f1.write_text("id,name\n1,Acme")
            f2.write_text("id,name\n2,Beta")
            assert IngestionPipeline.compute_hash(str(f1)) != \
                   IngestionPipeline.compute_hash(str(f2))

    def test_missing_file_returns_none(self):
        from app.services.ingestion_pipeline import IngestionPipeline
        result = IngestionPipeline.compute_hash("/no/such/file.csv")
        assert result is None


class TestRegisterFile:
    def _db_new(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.limit.return_value.execute.return_value.data = []
        db.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return db

    def _db_existing(self, file_id="existing-id"):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.limit.return_value.execute.return_value.data = [{"id": file_id, "status": "done"}]
        return db

    def test_new_file_returns_id_and_true(self):
        db = self._db_new()
        ip = _ip(db)
        file_id, is_new = ip.register_file("/data/customers.csv", "abc123")
        assert is_new is True
        assert file_id is not None and len(file_id) == 36

    def test_new_file_inserts_row(self):
        db = self._db_new()
        ip = _ip(db)
        ip.register_file("/data/customers.csv", "abc123", file_size=1024)
        db.table.return_value.insert.assert_called_once()
        row = db.table.return_value.insert.call_args[0][0]
        assert row["file_path"] == "/data/customers.csv"
        assert row["file_hash"] == "abc123"
        assert row["file_size"] == 1024
        assert row["entity_type"] == "customers"
        assert row["status"] == "pending"

    def test_duplicate_file_returns_existing_id_and_false(self):
        db = self._db_existing("old-id")
        ip = _ip(db)
        file_id, is_new = ip.register_file("/data/customers.csv", "abc123")
        assert is_new is False
        assert file_id == "old-id"
        db.table.return_value.insert.assert_not_called()

    def test_db_error_returns_none_and_false(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .eq.return_value.limit.return_value.execute.side_effect = RuntimeError("DB down")
        ip = _ip(db)
        file_id, is_new = ip.register_file("/data/file.csv", "hash")
        assert file_id is None
        assert is_new is False


class TestIngestionQueueOperations:
    def _db_for_get_pending(self, pending_rows, retry_rows=None):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .order.return_value.limit.return_value.execute.return_value.data = pending_rows
        db.table.return_value.select.return_value.eq.return_value.eq.return_value \
            .lte.return_value.order.return_value.limit.return_value.execute.return_value.data = \
            retry_rows or []
        return db

    def test_get_pending_returns_pending_rows(self):
        ip = _ip(self._db_for_get_pending([{"id": "f1"}, {"id": "f2"}]))
        rows = ip.get_pending(limit=10)
        assert len(rows) == 2

    def test_mark_processing_sets_status(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        ip = _ip(db)
        ip.mark_processing("file-1")
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["status"] == "processing"

    def test_mark_done_sets_rows_ingested(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        ip = _ip(db)
        ip.mark_done("file-1", 42)
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["status"] == "done"
        assert update_args["rows_ingested"] == 42
        assert update_args["error_detail"] is None

    def test_mark_error_below_max_sets_retry(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"attempt_count": 0, "max_attempts": 3}
        ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        ip = _ip(db)
        ip.mark_error("file-1", "parse failed", retry=True)
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["status"] == "error"
        assert update_args["next_retry_at"] is not None
        assert update_args["attempt_count"] == 1

    def test_mark_error_at_max_no_retry(self):
        db = _make_db()
        db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"attempt_count": 2, "max_attempts": 3}
        ]
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        ip = _ip(db)
        ip.mark_error("file-1", "always fails", retry=True)
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["next_retry_at"] is None

    def test_mark_skipped_sets_status(self):
        db = _make_db()
        db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
            MagicMock()
        ip = _ip(db)
        ip.mark_skipped("file-1", "unsupported format")
        update_args = db.table.return_value.update.call_args[0][0]
        assert update_args["status"] == "skipped"


# ═══════════════════════════════════════════════════════════════════════════════
#  IngestionScheduler
# ═══════════════════════════════════════════════════════════════════════════════

class TestIngestionScheduler:
    @pytest.mark.asyncio
    async def test_skip_file_on_unsupported_extension(self):
        from tasks.ingestion_scheduler import _ingest_file, _SkipFile
        with pytest.raises(_SkipFile):
            await _ingest_file(_make_db(), "co-1", "/data/report.docx")

    @pytest.mark.asyncio
    async def test_skip_file_on_empty_parse_result(self):
        from tasks.ingestion_scheduler import _ingest_file, _SkipFile
        with patch("app.file_ingestion.parsers.parse_file", return_value=None):
            with pytest.raises(_SkipFile):
                await _ingest_file(_make_db(), "co-1", "/data/report.csv")

    @pytest.mark.asyncio
    async def test_skip_file_on_no_rows(self):
        from tasks.ingestion_scheduler import _ingest_file, _SkipFile
        with patch("app.file_ingestion.parsers.parse_file",
                   return_value={"rows": [], "filename": "f.csv", "row_count": 0}):
            with pytest.raises(_SkipFile):
                await _ingest_file(_make_db(), "co-1", "/data/report.csv")

    @pytest.mark.asyncio
    async def test_process_company_counts_registered_and_processed(self):
        from tasks.ingestion_scheduler import _process_company

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create one CSV
            Path(tmpdir, "customers.csv").write_text("id,name\n1,Acme\n2,Beta")

            db = _make_db()
            # register_file: return (id, True) for new file
            db.table.return_value.select.return_value.eq.return_value.eq.return_value \
                .eq.return_value.limit.return_value.execute.return_value.data = []
            db.table.return_value.insert.return_value.execute.return_value = MagicMock()
            # get_pending returns the file
            db.table.return_value.select.return_value.eq.return_value.eq.return_value \
                .order.return_value.limit.return_value.execute.return_value.data = [
                {
                    "id": "file-1",
                    "file_path": str(Path(tmpdir, "customers.csv")),
                    "attempt_count": 0,
                    "max_attempts": 3,
                }
            ]
            db.table.return_value.select.return_value.eq.return_value.eq.return_value \
                .lte.return_value.order.return_value.limit.return_value.execute.return_value.data = []
            # mark_processing / mark_done updates
            db.table.return_value.update.return_value.eq.return_value.execute.return_value = \
                MagicMock()

            with patch("app.file_ingestion.parsers.parse_file",
                       return_value={"rows": [{"id": 1}, {"id": 2}], "filename": "customers.csv",
                                     "row_count": 2, "report_type": "customers"}), \
                 patch("tasks.file_ingestion._store_ingested_file",
                       return_value=None):
                result = await _process_company(db, "co-1", [tmpdir])

            assert result["ok"] is True
            assert result["registered_new"] >= 1
            assert result["errors"] == 0
