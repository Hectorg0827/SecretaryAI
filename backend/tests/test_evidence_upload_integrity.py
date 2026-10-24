"""Evidence uploads must hash real bytes and quarantine without claiming safety."""
import hashlib
import io
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile

os.environ.setdefault("SECRET_KEY", "test-secret-key-32-chars-long!!x")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.services.purchasing_evidence_bytes import (
    EVIDENCE_BUCKET, MAX_BYTES, inspect_uploaded_evidence,
    store_quarantined_evidence,
)
from app.api.logistics import (
    upload_purchasing_evidence_bytes, get_purchasing_evidence_integrity,
)


def file_for(content=b"%PDF-1.7\nclaim data"):
    return UploadFile(file=io.BytesIO(content), filename="not-trusted.pdf")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
    def select(self, *args):
        return self
    def eq(self, field, value):
        self.filters.append((field, value))
        return self
    def limit(self, n):
        return self
    def execute(self):
        return SimpleNamespace(data=self.rows)


class Database:
    def __init__(self, claim_id, payload, *, public=False, active=True):
        self.cid = str(uuid4())
        self.tables = {
            "purchasing_evidence_claims": [{
                "id": str(claim_id), "storage_object_key": self.cid+"/purchasing/a.pdf",
                "claimed_sha256": _sha(payload), "verification_status": "unverified",
            }],
            "purchasing_evidence_integrity_checks": [],
            "purchasing_evidence_malware_scans": [],
            "users": [{
                "id": "actor-1", "role": "back_office", "is_active": active,
            }],
        }
        self.queries = []
        self.rpc = MagicMock()
        self.rpc.return_value.execute.return_value.data = {
            "integrity_check_id": str(uuid4()),
            "integrity_status": "sha256_matched_at_upload",
            "malware_scan_status": "not_scanned",
        }
        self.storage = MagicMock()
        self.storage.get_bucket.return_value = SimpleNamespace(public=public)
    def table(self, name):
        q = Query(self.tables[name])
        self.queries.append((name,q))
        return q


def actor(db):
    return {"company_id": db.cid, "sub": "actor-1", "role": "back_office"}


@pytest.mark.asyncio
async def test_private_bucket_upload_with_real_hash_and_no_financial_approval():
    eid = uuid4()
    payload = b"%PDF-1.7\nexample evidence\n%%EOF"
    db = Database(eid,payload)
    out = await upload_purchasing_evidence_bytes(
        eid, file=file_for(payload), user=actor(db), db=db)
    assert out["integrity_status"] == "sha256_matched_at_upload"
    assert out["bytes_received"] == len(payload)
    assert out["safe_to_view"] is False
    assert out["malware_scan_status"] == "not_scanned"
    assert out["landed_cost_verified"] is False
    assert out["accounting_reconciled"] is False
    assert out["inventory_changed"] is False
    assert db.storage.get_bucket.call_args.args == (EVIDENCE_BUCKET,)
    args = db.storage.from_.return_value.upload.call_args.kwargs
    assert args["path"] == db.cid+"/purchasing/a.pdf"
    assert args["file"] == payload
    assert args["file_options"]["upsert"] == "false"
    assert args["file_options"]["content-type"] == "application/pdf"
    rpc, params = db.rpc.call_args.args
    assert rpc == "attest_purchasing_evidence_integrity"
    assert params["p_computed_sha256"] == _sha(payload)
    assert params["p_byte_count"] == len(payload)
    assert params["p_actor"] == "actor-1"
    assert params["p_company_id"] == db.cid
    assert all(("company_id",db.cid) in q.filters for _,q in db.queries)


@pytest.mark.asyncio
async def test_claimed_digest_mismatch_rejected_before_storage_and_rpc():
    eid = uuid4()
    db = Database(eid,b"%PDF-original")
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(b"%PDF-replacement"),user=actor(db),db=db)
    assert err.value.status_code == 422
    db.storage.get_bucket.assert_not_called()
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_payload_length_bounded_before_storage():
    eid=uuid4()
    blob=b"%PDF-"+b"x"*MAX_BYTES
    db=Database(eid,blob)
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(blob),user=actor(db),db=db)
    assert err.value.status_code == 413
    db.storage.get_bucket.assert_not_called()
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_private_bucket_requirement_and_no_false_integrity_result():
    eid=uuid4()
    blob=b"%PDF-test"
    db=Database(eid,blob,public=True)
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(blob),user=actor(db),db=db)
    assert err.value.status_code == 503
    db.storage.from_.assert_not_called()
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_mime_extension_rejected_before_storage():
    eid=uuid4()
    data=b"MZ"+b"x"*100
    db=Database(eid,data)
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(data),user=actor(db),db=db)
    assert err.value.status_code == 415
    db.storage.get_bucket.assert_not_called()


@pytest.mark.asyncio
async def test_cross_company_claim_not_found_and_no_mutation():
    eid=uuid4()
    blob=b"%PDF-test"
    db=Database(eid,blob)
    db.tables["purchasing_evidence_claims"]=[]
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(blob),user=actor(db),db=db)
    assert err.value.status_code == 404
    db.storage.get_bucket.assert_not_called()
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_revoked_database_user_cannot_upload():
    eid=uuid4()
    blob=b"%PDF-test"
    db=Database(eid,blob,active=False)
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(blob),user=actor(db),db=db)
    assert err.value.status_code == 403
    db.storage.get_bucket.assert_not_called()


@pytest.mark.asyncio
async def test_uploaded_evidence_cannot_be_overwritten():
    eid=uuid4()
    blob=b"%PDF-test"
    db=Database(eid,blob)
    db.tables["purchasing_evidence_integrity_checks"]=[{"id":str(uuid4())}]
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(blob),user=actor(db),db=db)
    assert err.value.status_code == 409
    db.storage.get_bucket.assert_not_called()
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_upload_error_cannot_falsely_attest_integrity():
    eid=uuid4()
    blob=b"%PDF-test"
    db=Database(eid,blob)
    db.storage.from_.return_value.upload.side_effect=ConnectionError("private credentials")
    with pytest.raises(HTTPException) as err:
        await upload_purchasing_evidence_bytes(
            eid,file=file_for(blob),user=actor(db),db=db)
    assert err.value.status_code==503
    assert "credentials" not in str(err.value.detail)
    db.rpc.assert_not_called()


@pytest.mark.asyncio
async def test_integrity_read_does_not_expose_file():
    eid=uuid4()
    blob=b"%PDF-test"
    db=Database(eid,blob)
    db.tables["purchasing_evidence_integrity_checks"]=[{
        "integrity_status":"sha256_matched_at_upload",
        "byte_count":len(blob), "malware_scan_status":"not_scanned",
    }]
    out=await get_purchasing_evidence_integrity(
        eid,user=actor(db),db=db)
    assert out["integrity_status"]=="sha256_matched_at_upload"
    assert out["safe_to_view"] is False
    assert out["accounting_reconciled"] is False
    assert out["malware_scan_status"] == "not_scanned"
    db.storage.from_.assert_not_called()

    # V9 scanned files must not be falsely reported as unscanned, and even
    # a clean result never grants a download or accounting authorization.
    db.tables["purchasing_evidence_malware_scans"]=[{"scan_status":"clean"}]
    checked=await get_purchasing_evidence_integrity(
        eid,user=actor(db),db=db)
    assert checked["malware_scan_status"] == "clean"
    assert checked["safe_to_view"] is False
    assert checked["accounting_reconciled"] is False
