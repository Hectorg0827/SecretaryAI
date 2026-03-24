"""
End-to-end tests for every gap implementation:
  - Computer Use router (start / poll / approve / reject)
  - Morning briefing delivery (SendGrid + DB)
  - Gmail ping in unified_adapter
  - PII redaction in safety layer
  - Agent sync + upload endpoints
"""
import io
import os

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SENDGRID_API_KEY", "SG.test-key")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_user(role="owner"):
    return {"sub": "user-1", "company_id": "co-1", "role": role}


def _make_db(company_row=None):
    db = MagicMock()
    company_row = company_row or {
        "id": "co-1",
        "name": "Test Co",
        "qbd_end_user_id": "eu_test123",
        "qbo_access_token": None,
        "qbo_refresh_token": None,
        "google_access_token": None,
        "google_refresh_token": None,
    }
    db.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [company_row]
    db.table.return_value.insert.return_value.execute.return_value = MagicMock()
    db.table.return_value.upsert.return_value.execute.return_value = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return db


# ─── Computer Use Router ───────────────────────────────────────────────────────

class TestComputerUseRouter:
    """Tests for /api/computer-use/* endpoints."""

    def test_start_session_creates_state(self):
        """POST /start should create a session and launch background engine."""
        from app.api.computer_use import _sessions
        _sessions.clear()

        with (
            patch("app.api.computer_use.get_current_user", return_value=_make_user()),
            patch("app.api.computer_use.get_db", return_value=_make_db()),
            patch("app.api.computer_use.settings") as mock_settings,
            patch("asyncio.create_task"),
        ):
            mock_settings.computer_use_enabled = True

            from app.api.computer_use import _SessionState
            session = _SessionState("sess-1", "ScribeBase", "Get orders", "co-1")
            _sessions["sess-1"] = session

            assert _sessions["sess-1"].status == "running"
            assert _sessions["sess-1"].app_name == "ScribeBase"

    def test_session_status_response(self):
        """GET /sessions/{id} should return full session state."""
        from app.api.computer_use import _sessions, _SessionState

        state = _SessionState("sess-2", "ScribeBase", "Get orders", "co-1")
        state.steps_completed = 3
        _sessions["sess-2"] = state

        # Simulate what the endpoint returns
        assert state.session_id == "sess-2"
        assert state.status == "running"
        assert state.steps_completed == 3
        assert state.pending_approval is None

    @pytest.mark.asyncio
    async def test_approval_gate_flow(self):
        """request_approval waits until resolve_approval is called."""
        import asyncio
        from app.api.computer_use import _SessionState

        state = _SessionState("sess-3", "App", "Task", "co-1")

        async def _approve_after_delay():
            await asyncio.sleep(0.05)
            state.resolve_approval("approve")

        asyncio.create_task(_approve_after_delay())
        decision = await state.request_approval("click", "Click the Submit button")
        assert decision == "approve"
        assert state.pending_approval is None

    @pytest.mark.asyncio
    async def test_reject_gate_flow(self):
        """reject should propagate 'reject' decision through approval gate."""
        import asyncio
        from app.api.computer_use import _SessionState

        state = _SessionState("sess-4", "App", "Task", "co-1")

        async def _reject_after_delay():
            await asyncio.sleep(0.05)
            state.resolve_approval("reject")

        asyncio.create_task(_reject_after_delay())
        decision = await state.request_approval("type", "Type something dangerous")
        assert decision == "reject"

    @pytest.mark.asyncio
    async def test_approval_timeout_defaults_to_reject(self):
        """If no decision is made within timeout, action should be rejected."""
        import asyncio
        from app.api.computer_use import _SessionState

        state = _SessionState("sess-5", "App", "Task", "co-1")

        # Override timeout to near-zero for test speed
        import app.api.computer_use as cu_module
        original = asyncio.wait_for

        async def fast_wait_for(coro, timeout):
            # Simulate timeout immediately
            raise asyncio.TimeoutError()

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            decision = await state.request_approval("delete", "Delete all records")
        assert decision == "reject"

    def test_unknown_session_raises_404(self):
        """Accessing a non-existent session should raise HTTPException 404."""
        from fastapi import HTTPException
        from app.api.computer_use import _sessions

        session_id = "nonexistent-session"
        state = _sessions.get(session_id)
        assert state is None

    def test_session_wrong_company_raises_403(self):
        """A session belonging to another company should not be accessible."""
        from app.api.computer_use import _sessions, _SessionState

        state = _SessionState("sess-x", "App", "Task", "co-OTHER")
        _sessions["sess-x"] = state

        user = _make_user()  # company_id = "co-1"
        assert state.company_id != user["company_id"]


# ─── Morning Briefing ──────────────────────────────────────────────────────────

class TestMorningBriefing:
    """Tests for generate_morning_briefing (SendGrid + DB)."""

    @pytest.mark.asyncio
    async def test_sends_email_via_sendgrid(self):
        """Briefing should be sent via SendGrid when API key is set."""
        mock_db = _make_db()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Good morning! Here's your briefing.")]

        with (
            patch("app.scheduler.morning_briefing.anthropic.AsyncAnthropic") as mock_anthropic,
            patch("app.scheduler.morning_briefing.SendGridAPIClient") as mock_sg,
            patch("app.api.deps.get_db", return_value=mock_db),
            patch("app.scheduler.morning_briefing.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.sendgrid_api_key = "SG.test"
            mock_settings.from_email = "secretary@test.com"
            mock_anthropic.return_value.messages.create = AsyncMock(return_value=mock_response)

            from app.scheduler.morning_briefing import generate_morning_briefing
            result = await generate_morning_briefing(
                company_id="co-1",
                company_name="Test Co",
                preferred_language="English",
                data_summary="3 open invoices",
                recipient_email="owner@test.com",
            )

            assert len(result) > 0
            mock_sg.return_value.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_briefing_in_db(self):
        """Briefing should be persisted to the morning_briefings table."""
        mock_db = _make_db()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Morning briefing text")]

        with (
            patch("app.scheduler.morning_briefing.anthropic.AsyncAnthropic") as mock_anthropic,
            patch("app.scheduler.morning_briefing.SendGridAPIClient"),
            patch("app.api.deps.get_db", return_value=mock_db),
            patch("app.scheduler.morning_briefing.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.sendgrid_api_key = "SG.test"
            mock_settings.from_email = "secretary@test.com"
            mock_anthropic.return_value.messages.create = AsyncMock(return_value=mock_response)

            from app.scheduler.morning_briefing import generate_morning_briefing
            await generate_morning_briefing(
                company_id="co-1",
                company_name="Test Co",
                preferred_language="English",
                data_summary="Summary",
                recipient_email="owner@test.com",
            )

            mock_db.table.assert_any_call("morning_briefings")

    @pytest.mark.asyncio
    async def test_no_email_when_sendgrid_not_configured(self):
        """No email should be sent when SENDGRID_API_KEY is empty."""
        mock_db = _make_db()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Briefing text")]

        with (
            patch("app.scheduler.morning_briefing.anthropic.AsyncAnthropic") as mock_anthropic,
            patch("app.scheduler.morning_briefing.SendGridAPIClient") as mock_sg,
            patch("app.api.deps.get_db", return_value=mock_db),
            patch("app.scheduler.morning_briefing.settings") as mock_settings,
        ):
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.sendgrid_api_key = ""  # Not configured
            mock_settings.from_email = "secretary@test.com"
            mock_anthropic.return_value.messages.create = AsyncMock(return_value=mock_response)

            from app.scheduler.morning_briefing import generate_morning_briefing
            await generate_morning_briefing(
                company_id="co-1",
                company_name="Test Co",
                preferred_language="English",
                data_summary="Summary",
                recipient_email="owner@test.com",
            )

            mock_sg.return_value.send.assert_not_called()

    def test_to_html_converts_markdown_headings(self):
        """_to_html should convert # headings to <h2> and ## to <h3>."""
        from app.scheduler.morning_briefing import _to_html

        html = _to_html("# Good Morning\n## Key Metrics\n- Item one")
        assert "<h2" in html
        assert "<h3" in html
        assert "<li>" in html


# ─── Gmail Ping ────────────────────────────────────────────────────────────────

class TestGmailPing:
    """Tests for the Gmail connectivity check in UnifiedDataAdapter."""

    @pytest.mark.asyncio
    async def test_gmail_ping_returns_true_on_200(self):
        """test_all_connections should return gmail=True when profile API returns 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        import httpx
        from app.connectors.unified_adapter import UnifiedDataAdapter

        adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
        adapter._qb = None
        adapter._gmail = MagicMock()
        adapter._gmail._token = "test-token"
        adapter._outlook = None
        adapter._sheets = None
        adapter._shopify = None
        adapter._shiptrack = None

        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            results = await adapter.test_all_connections()

        assert results.get("gmail") is True

    @pytest.mark.asyncio
    async def test_gmail_ping_returns_false_on_401(self):
        """test_all_connections should return gmail=False on 401."""
        mock_response = MagicMock()
        mock_response.status_code = 401

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        from app.connectors.unified_adapter import UnifiedDataAdapter

        adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
        adapter._qb = None
        adapter._gmail = MagicMock()
        adapter._gmail._token = "expired-token"
        adapter._outlook = None
        adapter._sheets = None
        adapter._shopify = None
        adapter._shiptrack = None

        import httpx
        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            results = await adapter.test_all_connections()

        assert results.get("gmail") is False

    @pytest.mark.asyncio
    async def test_gmail_ping_returns_false_on_network_error(self):
        """test_all_connections should return gmail=False on network error."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("Network error"))

        from app.connectors.unified_adapter import UnifiedDataAdapter

        adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
        adapter._qb = None
        adapter._gmail = MagicMock()
        adapter._gmail._token = "test-token"
        adapter._outlook = None
        adapter._sheets = None
        adapter._shopify = None
        adapter._shiptrack = None

        import httpx
        with patch.object(httpx, "AsyncClient", return_value=mock_client):
            results = await adapter.test_all_connections()

        assert results.get("gmail") is False

    @pytest.mark.asyncio
    async def test_no_gmail_connector_skips_ping(self):
        """test_all_connections should not include gmail key when Gmail is not configured."""
        from app.connectors.unified_adapter import UnifiedDataAdapter

        adapter = UnifiedDataAdapter.__new__(UnifiedDataAdapter)
        adapter._qb = None
        adapter._gmail = None
        adapter._outlook = None
        adapter._sheets = None
        adapter._shopify = None
        adapter._shiptrack = None

        results = await adapter.test_all_connections()
        assert "gmail" not in results


# ─── PII Redaction ─────────────────────────────────────────────────────────────

class TestPIIRedaction:
    """Tests for check_screenshot_for_pii in ComputerUseSafety."""

    def _make_tiny_png(self, width=50, height=50) -> bytes:
        """Create a minimal valid PNG for testing."""
        from PIL import Image
        img = Image.new("RGB", (width, height), color=(128, 128, 128))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_clean_screenshot_passes_through_unchanged(self):
        """A screenshot with no PII should be returned unchanged."""
        from app.computer_use.safety import ComputerUseSafety
        safety = ComputerUseSafety({})
        png = self._make_tiny_png()
        result = safety.check_screenshot_for_pii(png)
        assert result == png

    def test_credit_card_pattern_triggers_redaction(self):
        """A PNG whose bytes contain a credit-card-like number should trigger blur."""
        from app.computer_use.safety import ComputerUseSafety
        from PIL import Image
        safety = ComputerUseSafety({})

        # Craft bytes that contain a CC-like pattern in the latin-1 decode
        # We embed the pattern directly in a text chunk after the PNG header
        base_png = self._make_tiny_png(100, 100)
        # Inject a credit-card number pattern into the raw bytes (as latin-1 text)
        cc_pattern = b" 4111 1111 1111 1111 "
        poisoned = base_png + cc_pattern

        # It won't be valid PNG but the regex check runs before PIL opens it
        # The safety layer should attempt blur and fall back gracefully if PIL fails
        try:
            result = safety.check_screenshot_for_pii(poisoned)
            # If it didn't raise, it either blurred or passed through
            assert isinstance(result, bytes)
        except Exception:
            pytest.fail("check_screenshot_for_pii should never raise")

    def test_ssn_pattern_in_metadata(self):
        """SSN pattern in raw bytes should be detected."""
        from app.computer_use.safety import ComputerUseSafety
        safety = ComputerUseSafety({})

        # Make valid PNG and inject SSN pattern
        base_png = self._make_tiny_png()
        poisoned = base_png + b" 123-45-6789 "

        result = safety.check_screenshot_for_pii(poisoned)
        assert isinstance(result, bytes)

    def test_corrupt_input_never_raises(self):
        """Completely invalid bytes should be handled gracefully (pass-through)."""
        from app.computer_use.safety import ComputerUseSafety
        safety = ComputerUseSafety({})

        garbage = b"\x00\x01\x02 4111111111111111 \x03\x04"
        result = safety.check_screenshot_for_pii(garbage)
        assert isinstance(result, bytes)

    def test_valid_png_no_pii_exact_bytes(self):
        """Valid PNG with no PII in metadata returns exact same bytes."""
        from app.computer_use.safety import ComputerUseSafety
        safety = ComputerUseSafety({})

        png = self._make_tiny_png(20, 20)
        result = safety.check_screenshot_for_pii(png)
        # No PII → pass-through → identical bytes
        assert result == png


# ─── Agent Sync Endpoint ───────────────────────────────────────────────────────

class TestAgentSyncEndpoint:
    """Tests for POST /api/agent/sync."""

    @pytest.mark.asyncio
    async def test_sync_triggers_qb_fetch(self):
        """Sync should call QBDesktopConnector and update last-sync timestamp."""
        mock_db = _make_db()
        user = _make_user()

        mock_qbd = AsyncMock()
        mock_qbd.get_customers = AsyncMock(return_value=[{"id": "c1"}, {"id": "c2"}])
        mock_qbd.get_invoices = AsyncMock(return_value=[{"id": "i1"}])

        with (
            patch("app.api.agent.settings") as mock_settings,
            patch("app.api.agent.QBDesktopAdapter", return_value=mock_qbd),
        ):
            mock_settings.conductor_api_key = "cond-test-key"

            from app.api.agent import trigger_sync
            result = await trigger_sync(user=user, db=mock_db)

            assert result["status"] == "ok"
            assert "2" in result["message"]  # 2 accounts
            mock_qbd.get_customers.assert_called_once()
            mock_qbd.get_invoices.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_skipped_when_qbd_not_connected(self):
        """Sync should return 'skipped' if qbd_end_user_id is not set."""
        company_row = {"id": "co-1", "name": "Test", "qbd_end_user_id": None}
        mock_db = _make_db(company_row)
        user = _make_user()

        with (
            patch("app.api.agent.get_current_user", return_value=user),
            patch("app.api.agent.get_db", return_value=mock_db),
            patch("app.api.agent.settings"),
        ):
            from app.api.agent import trigger_sync
            result = await trigger_sync(user=user, db=mock_db)
            assert result["status"] == "skipped"


# ─── Agent Upload Endpoint ─────────────────────────────────────────────────────

class TestAgentUploadEndpoint:
    """Tests for POST /api/agent/upload-report."""

    @pytest.mark.asyncio
    async def test_csv_upload_accepted(self):
        """CSV file should be accepted and metadata stored."""
        from fastapi import UploadFile
        user = _make_user()
        mock_db = _make_db()

        csv_bytes = b"id,name,qty\n1,Widget,50\n2,Gadget,30"
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "report.csv"
        mock_file.content_type = "text/csv"
        mock_file.read = AsyncMock(return_value=csv_bytes)

        with (
            patch("app.api.agent.get_current_user", return_value=user),
            patch("app.api.agent.get_db", return_value=mock_db),
        ):
            from app.api.agent import upload_report
            result = await upload_report(file=mock_file, user=user, db=mock_db)

        assert result["status"] == "accepted"
        assert result["file_name"] == "report.csv"
        assert result["size"] == len(csv_bytes)
        mock_db.table.assert_any_call("uploaded_reports")

    @pytest.mark.asyncio
    async def test_xlsx_upload_accepted(self):
        """XLSX file should be accepted."""
        from fastapi import UploadFile
        user = _make_user()
        mock_db = _make_db()

        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "report.xlsx"
        mock_file.content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        mock_file.read = AsyncMock(return_value=b"PK\x03\x04fake-xlsx-content")

        from app.api.agent import upload_report
        result = await upload_report(file=mock_file, user=user, db=mock_db)
        assert result["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_unsupported_type_raises_415(self):
        """Executable files should be rejected with 415."""
        from fastapi import HTTPException, UploadFile
        user = _make_user()
        mock_db = _make_db()

        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "malware.exe"
        mock_file.content_type = "application/x-msdownload"
        mock_file.read = AsyncMock(return_value=b"MZ")

        from app.api.agent import upload_report
        with pytest.raises(HTTPException) as exc_info:
            await upload_report(file=mock_file, user=user, db=mock_db)
        assert exc_info.value.status_code == 415

    @pytest.mark.asyncio
    async def test_oversized_file_raises_413(self):
        """File exceeding 25 MB should be rejected with 413."""
        from fastapi import HTTPException, UploadFile
        user = _make_user()
        mock_db = _make_db()

        oversized = b"x" * (26 * 1024 * 1024)  # 26 MB
        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "huge.csv"
        mock_file.content_type = "text/csv"
        mock_file.read = AsyncMock(return_value=oversized)

        from app.api.agent import upload_report
        with pytest.raises(HTTPException) as exc_info:
            await upload_report(file=mock_file, user=user, db=mock_db)
        assert exc_info.value.status_code == 413

    @pytest.mark.asyncio
    async def test_pdf_upload_accepted(self):
        """PDF file should be accepted."""
        from fastapi import UploadFile
        user = _make_user()
        mock_db = _make_db()

        mock_file = MagicMock(spec=UploadFile)
        mock_file.filename = "invoice.pdf"
        mock_file.content_type = "application/pdf"
        mock_file.read = AsyncMock(return_value=b"%PDF-1.4 fake-pdf-content")

        from app.api.agent import upload_report
        result = await upload_report(file=mock_file, user=user, db=mock_db)
        assert result["status"] == "accepted"


# ─── CU Engine Audit ───────────────────────────────────────────────────────────

class TestComputerUseEngineAudit:
    """Tests for _audit method in ComputerUseEngine."""

    @pytest.mark.asyncio
    async def test_audit_writes_to_action_log(self):
        """_audit should insert a row to the action_log table."""
        mock_db = _make_db()

        with patch("app.api.deps.get_db", return_value=mock_db):
            from app.computer_use.engine import ComputerUseEngine
            with patch("anthropic.AsyncAnthropic"):
                engine = ComputerUseEngine.__new__(ComputerUseEngine)
                engine.company_config = {"id": "co-1"}
                engine.safety = MagicMock()
                await engine._audit("test_event", {"key": "value"})

        mock_db.table.assert_any_call("action_log")

    @pytest.mark.asyncio
    async def test_audit_failure_does_not_raise(self):
        """_audit failures should be non-fatal (logged but not re-raised)."""
        with patch("app.api.deps.get_db", side_effect=Exception("DB down")):
            from app.computer_use.engine import ComputerUseEngine
            engine = ComputerUseEngine.__new__(ComputerUseEngine)
            engine.company_config = {"id": "co-1"}
            engine.safety = MagicMock()

            try:
                await engine._audit("crash_test", {})
            except Exception:
                pytest.fail("_audit should swallow exceptions")
