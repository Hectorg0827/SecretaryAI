"""Staging-only contract checks for Lola's JSONB conversation persistence and isolation."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.chat import (
    ChatRequest, _load_history, _save_turn,
    get_conversation_history, list_conversations, send_message,
)


class ConversationQuery:
    def __init__(self, db):
        self.db = db
        self.filters = []
        self.operation = "read"
        self.payload = None
        self.max_rows = None

    def select(self, *_):
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def limit(self, count):
        self.max_rows = count
        return self

    def order(self, *_args, **_kwargs):
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def execute(self):
        selected = [row for row in self.db.rows
                    if all(row.get(field) == value for field, value in self.filters)]
        if self.max_rows is not None:
            selected = selected[:self.max_rows]
        if self.operation == "update":
            for row in selected:
                row.update(self.payload)
            return SimpleNamespace(data=selected)
        if self.operation == "insert":
            if any(row["id"] == self.payload["id"] for row in self.db.rows):
                raise ValueError("Duplicate conversation ID")
            row = {"title": None, "created_at": None, "updated_at": None, **self.payload}
            self.db.rows.append(row)
            return SimpleNamespace(data=[row])
        return SimpleNamespace(data=selected)


class ConversationDB:
    def __init__(self):
        self.rows = []

    def table(self, name):
        assert name == "conversations", "Unexpected database table accessed by test"
        return ConversationQuery(self)


def test_jsonb_history_load_and_save_are_tenant_and_user_scoped():
    db = ConversationDB()
    _save_turn(db, "first", "company-a", "alice", "Question 1", "Answer 1")
    _save_turn(db, "first", "company-a", "alice", "Question 2", "Answer 2")
    assert _load_history(db, "first", "company-a", "alice") == [
        {"role": "user", "content": "Question 1"},
        {"role": "assistant", "content": "Answer 1"},
        {"role": "user", "content": "Question 2"},
        {"role": "assistant", "content": "Answer 2"},
    ]
    assert _load_history(db, "first", "company-b", "alice") == []
    assert _load_history(db, "first", "company-a", "bob") == []


@pytest.mark.asyncio
async def test_history_and_listing_never_return_another_users_data():
    db = ConversationDB()
    _save_turn(db, "private", "company-a", "alice", "Private", "Secret")
    me = {"sub": "alice", "company_id": "company-a"}
    other = {"sub": "bob", "company_id": "company-a"}
    tenant = {"sub": "alice", "company_id": "company-b"}
    history = await get_conversation_history("private", limit=40, user=me, db=db)
    assert [m["content"] for m in history["messages"]] == ["Private", "Secret"]
    listing = await list_conversations(limit=20, user=me, db=db)
    assert listing["conversations"][0]["conversation_id"] == "private"
    assert (await list_conversations(limit=20, user=other, db=db))["conversations"] == []
    for denied in (other, tenant):
        with pytest.raises(HTTPException) as exc:
            await get_conversation_history("private", limit=40, user=denied, db=db)
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_stream_creates_a_conversation_with_persisted_turn():
    db = ConversationDB()
    user = {"sub": "alice", "company_id": "company-a", "role": "owner"}

    async def fake_stream(*_args):
        yield "A business"
        yield " answer."

    with patch("app.api.chat.classify_intent", new=AsyncMock(return_value="general_question")), \
         patch("app.api.chat._fetch_data_for_intent", new=AsyncMock(return_value={})), \
         patch("app.api.chat.build_query_context", return_value=""), \
         patch("app.api.chat.load_recent_summaries", return_value=[]), \
         patch("app.api.chat.load_account_memory", return_value=[]), \
         patch("app.api.chat.stream_chat", side_effect=fake_stream), \
         patch("app.api.chat.detect_action_in_response", return_value=None):
        response = await send_message(ChatRequest(message="What needs attention?"), user=user, adapter=None, db=db)
        chunks = [part async for part in response.body_iterator]
    payload = "".join(part.decode() if isinstance(part, bytes) else part for part in chunks)
    assert "A business" in payload and " answer." in payload
    assert '"done": true' in payload
    assert len(db.rows) == 1
    conv = db.rows[0]
    assert conv["user_id"] == "alice" and conv["company_id"] == "company-a"
    assert [m["content"] for m in conv["messages"]] == [
        "What needs attention?", "A business answer."
    ]


@pytest.mark.asyncio
async def test_send_rejects_foreign_conversation_id_before_ai_runs():
    db = ConversationDB()
    _save_turn(db, "foreign", "company-a", "alice", "Confidential", "Answer")
    with patch("app.api.chat.classify_intent", new=AsyncMock()) as classifier:
        with pytest.raises(HTTPException) as exc:
            await send_message(
                ChatRequest(message="Read private chat", conversation_id="foreign"),
                user={"sub": "bob", "company_id": "company-a"},
                adapter=None, db=db,
            )
        assert exc.value.status_code == 404
        classifier.assert_not_awaited()
    assert db.rows[0]["messages"][0]["content"] == "Confidential"


@pytest.mark.asyncio
async def test_sales_rep_general_chat_cannot_retrieve_business_or_memory_data():
    db = ConversationDB()

    async def fake_stream(*_args):
        yield "No verified business data is available."

    with patch("app.api.chat.classify_intent", new=AsyncMock(return_value="general_question")), \
         patch("app.api.chat._fetch_data_for_intent", new=AsyncMock()) as data_fetch, \
         patch("app.api.chat.load_recent_summaries") as memory_fetch, \
         patch("app.api.chat.load_account_memory") as account_memory_fetch, \
         patch("app.api.chat.stream_chat", side_effect=fake_stream), \
         patch("app.api.chat.detect_action_in_response", return_value=None):
        response = await send_message(
            ChatRequest(message="Show company revenue"),
            user={"sub": "rep", "company_id": "company-a", "role": "sales_rep"},
            adapter=None, db=db,
        )
        body = [part async for part in response.body_iterator]
        assert any("No verified business data" in str(part) for part in body)
        data_fetch.assert_not_awaited()
        memory_fetch.assert_not_called()
        account_memory_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_sales_rep_financial_intent_fails_before_querying_connector():
    db = ConversationDB()
    with patch("app.api.chat.classify_intent", new=AsyncMock(return_value="sales_report")), \
         patch("app.api.chat._fetch_data_for_intent", new=AsyncMock()) as data_fetch:
        with pytest.raises(HTTPException) as exc:
            await send_message(
                ChatRequest(message="What is our company-wide revenue?"),
                user={"sub": "rep", "company_id": "company-a", "role": "sales_rep"},
                adapter=None, db=db,
            )
        assert exc.value.status_code == 403
        data_fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_reports_persistence_failure_instead_of_claiming_completion():
    db = ConversationDB()

    async def fake_stream(*_args):
        yield "Unsaved answer"

    with (
        patch("app.api.chat.classify_intent", new=AsyncMock(return_value="general_question")),
        patch("app.api.chat._fetch_data_for_intent", new=AsyncMock(return_value={})),
        patch("app.api.chat.build_query_context", return_value=""),
        patch("app.api.chat.load_recent_summaries", return_value=[]),
        patch("app.api.chat.load_account_memory", return_value=[]),
        patch("app.api.chat.stream_chat", side_effect=fake_stream),
        patch("app.api.chat._save_turn", side_effect=RuntimeError("Synthetic database outage")),
    ):
        response = await send_message(
            ChatRequest(message="Question"),
            user={"sub": "alice", "company_id": "company-a", "role": "owner"},
            adapter=None, db=db,
        )
        payload = "".join(str(chunk) for chunk in [part async for part in response.body_iterator])
    assert "Conversation could not be saved" in payload
    assert '"done": true' not in payload


@pytest.mark.asyncio
async def test_general_business_context_accepts_object_invoices_with_zero_and_empty_items():
    from datetime import date
    from decimal import Decimal
    from app.api.chat import _fetch_data_for_intent
    inv = SimpleNamespace(
        customer_id="synthetic-customer", customer_name="Synthetic",
        date=date.today(), total=Decimal("0"), line_items=[],
    )
    adapter = SimpleNamespace(
        get_all_customers=AsyncMock(return_value=[]),
        get_orders_last_n_days=AsyncMock(return_value=[inv]),
        get_inventory_merged=AsyncMock(return_value=[]),
    )
    data = await _fetch_data_for_intent("general_question", "Today's status", adapter)
    assert data["sales_summary"]["order_count"] == 1
    assert data["sales_summary"]["total_revenue"] == 0
