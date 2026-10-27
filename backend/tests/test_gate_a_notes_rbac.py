"""Gate A: a representative may not enumerate or change another user's notes.

All users, records and tenants are synthetic. No external connections or
database writes are executed; query methods are mocked.
"""
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.dashboard import (
    CreateNoteRequest, UpdateNoteRequest, create_note, list_notes, update_note,
)


def fake_db(rows):
    db = MagicMock()
    query = MagicMock()
    for method in ("select", "eq", "order", "update", "insert"):
        getattr(query, method).return_value = query
    query.execute.return_value.data = rows
    db.table.return_value = query
    return db, query


@pytest.mark.asyncio
async def test_representative_notes_are_filtered_by_tenant_and_owner():
    db, query = fake_db([{
        "id": "my-note", "company_id": "company-1", "user_id": "rep-1",
        "text": "Synthetic follow-up",
    }])
    result = await list_notes(
        user={"company_id": "company-1", "sub": "rep-1", "role": "sales_rep"},
        db=db,
    )
    assert len(result["notes"]) == 1
    assert ("company_id", "company-1") in [
        c.args for c in query.eq.call_args_list
    ]
    assert ("user_id", "rep-1") in [
        c.args for c in query.eq.call_args_list
    ]


@pytest.mark.asyncio
async def test_manager_notes_are_company_scoped_without_rep_ownership_filter():
    db, query = fake_db([])
    await list_notes(
        user={"company_id": "company-1", "sub": "manager-1", "role": "manager"},
        db=db,
    )
    assert ("company_id", "company-1") in [
        c.args for c in query.eq.call_args_list
    ]
    assert all(c.args[0] != "user_id" for c in query.eq.call_args_list)


@pytest.mark.asyncio
async def test_notes_read_provider_failure_is_503_not_empty_list():
    db, query = fake_db([])
    query.execute.side_effect = ConnectionError("Fixture database offline")
    with pytest.raises(HTTPException) as caught:
        await list_notes(
            user={"company_id": "company-1", "sub": "rep-1", "role": "sales_rep"},
            db=db,
        )
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_rep_update_is_scoped_to_note_tenant_and_owner():
    db, query = fake_db([{
        "id": "my-note", "company_id": "company-1", "user_id": "rep-1",
        "done": True,
    }])
    response = await update_note(
        "my-note", UpdateNoteRequest(done=True),
        user={"company_id": "company-1", "sub": "rep-1", "role": "sales_rep"},
        db=db,
    )
    assert response["done"] is True
    assert ("id", "my-note") in [c.args for c in query.eq.call_args_list]
    assert ("company_id", "company-1") in [c.args for c in query.eq.call_args_list]
    assert ("user_id", "rep-1") in [c.args for c in query.eq.call_args_list]


@pytest.mark.asyncio
async def test_filtered_out_note_update_returns_404_not_false_success():
    db, query = fake_db([])
    with pytest.raises(HTTPException) as caught:
        await update_note(
            "another-users-note", UpdateNoteRequest(done=True),
            user={"company_id": "company-1", "sub": "rep-1", "role": "sales_rep"},
            db=db,
        )
    assert caught.value.status_code == 404
    assert ("user_id", "rep-1") in [c.args for c in query.eq.call_args_list]


@pytest.mark.asyncio
async def test_notes_update_provider_error_returns_503():
    db, query = fake_db([])
    query.execute.side_effect = RuntimeError("Fixture storage offline")
    with pytest.raises(HTTPException) as caught:
        await update_note(
            "my-note", UpdateNoteRequest(done=True),
            user={"company_id": "company-1", "sub": "rep-1", "role": "sales_rep"},
            db=db,
        )
    assert caught.value.status_code == 503



@pytest.mark.asyncio
async def test_unconfirmed_new_note_is_not_reported_as_saved():
    db, query = fake_db([])
    with pytest.raises(HTTPException) as caught:
        await create_note(
            CreateNoteRequest(text="Synthetic follow-up"),
            user={"company_id": "company-1", "sub": "rep-1", "role": "sales_rep"},
            db=db,
        )
    assert caught.value.status_code == 503
    assert query.insert.call_args.args[0]["user_id"] == "rep-1"
    assert query.insert.call_args.args[0]["company_id"] == "company-1"


@pytest.mark.asyncio
async def test_confirmed_note_creation_uses_tenant_and_actor():
    db, query = fake_db([{
        "id": "new-note", "company_id": "company-1", "user_id": "rep-1",
        "text": "Synthetic follow-up",
    }])
    result = await create_note(
        CreateNoteRequest(text="Synthetic follow-up"),
        user={"company_id": "company-1", "sub": "rep-1", "role": "sales_rep"},
        db=db,
    )
    assert result["id"] == "new-note"
    assert query.insert.call_args.args[0]["user_id"] == "rep-1"
