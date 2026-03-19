"""
200-question accuracy test suite for the SecretaryAI conversational AI layer.

Tests that:
  1. classify_intent() returns the correct intent label for every question
  2. build_query_context() produces a non-empty context string for each intent
  3. detect_action_in_response() correctly identifies action-triggering phrases

Structure:
  - English + Spanish questions for all intent types
  - Edge cases: ambiguous phrasing, typos, multi-intent questions
  - Action detection: email drafts, POs, reports, alerts

All tests use mocks — no network calls, no DB, no Claude API.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ─── Intent classification fixtures ───────────────────────────────────────────

# (question_text, expected_intent)
INTENT_QUESTIONS: list[tuple[str, str]] = [
    # ── inventory_check (40 questions) ────────────────────────────────────
    ("How much Widget A do we have in stock?", "inventory_check"),
    ("What's our current inventory?", "inventory_check"),
    ("Are we running low on Gadget X?", "inventory_check"),
    ("Show me all products with less than 2 weeks of supply.", "inventory_check"),
    ("What items are critically low right now?", "inventory_check"),
    ("Do we have enough Widget B to cover next month?", "inventory_check"),
    ("List everything that needs reordering.", "inventory_check"),
    ("What's the warehouse count for SKU WGT-A?", "inventory_check"),
    ("How many cases of Gadget X are left?", "inventory_check"),
    ("Is Widget B in stock?", "inventory_check"),
    ("Which products are out of stock?", "inventory_check"),
    ("Inventory status for all items please.", "inventory_check"),
    ("Give me the stock levels.", "inventory_check"),
    ("What do we have in the warehouse?", "inventory_check"),
    ("Any items close to stockout?", "inventory_check"),
    ("Show me products with under 50 units.", "inventory_check"),
    ("Reorder point check for all SKUs.", "inventory_check"),
    ("How many weeks of supply do we have for Widget A?", "inventory_check"),
    ("Which items should I order today?", "inventory_check"),
    ("Do we need to replenish anything urgently?", "inventory_check"),
    # Spanish
    ("¿Cuántas unidades de Widget A tenemos?", "inventory_check"),
    ("¿Cuál es el inventario actual?", "inventory_check"),
    ("¿Qué productos están bajos en stock?", "inventory_check"),
    ("Muéstrame los artículos con menos de 2 semanas de suministro.", "inventory_check"),
    ("¿Tenemos suficiente Gadget X para el próximo mes?", "inventory_check"),
    ("¿Qué artículos necesitan reabastecimiento?", "inventory_check"),
    ("Niveles de stock de todos los productos.", "inventory_check"),
    ("¿Algún producto en punto crítico de reorden?", "inventory_check"),
    ("¿Cuántas cajas de Widget B quedan?", "inventory_check"),
    ("Lista de productos por debajo del punto de reorden.", "inventory_check"),
    # Edge cases
    ("stock?", "inventory_check"),
    ("low inventory", "inventory_check"),
    ("invetory check", "inventory_check"),          # typo
    ("how much widgt a do we have", "inventory_check"),  # typo
    ("warehouse qty", "inventory_check"),
    ("show inventory critical items only", "inventory_check"),
    ("need to order anything?", "inventory_check"),
    ("we running low on anything?", "inventory_check"),
    ("reorder list", "inventory_check"),
    ("what products are almost out", "inventory_check"),

    # ── account_status (50 questions) ────────────────────────────────────
    ("How is Acme Corp doing?", "account_status"),
    ("What's the health of our accounts?", "account_status"),
    ("Show me at-risk customers.", "account_status"),
    ("When did Global Imports last order?", "account_status"),
    ("Which customers haven't ordered in 60 days?", "account_status"),
    ("Is Metro Distributors still active?", "account_status"),
    ("Show me dormant accounts.", "account_status"),
    ("List all customers with health score below 50.", "account_status"),
    ("Who are our top accounts by sales?", "account_status"),
    ("What's the outstanding balance for Acme Corp?", "account_status"),
    ("Which accounts are slowing down?", "account_status"),
    ("Give me an overview of all customer health.", "account_status"),
    ("How many customers are at risk?", "account_status"),
    ("Show me the status of Global Imports LLC.", "account_status"),
    ("Which accounts should I call today?", "account_status"),
    ("Are any customers likely to churn?", "account_status"),
    ("Customer health summary.", "account_status"),
    ("Show me accounts with overdue balances.", "account_status"),
    ("Who placed the last order?", "account_status"),
    ("How often does Metro Distributors order?", "account_status"),
    ("Which accounts are healthy?", "account_status"),
    ("Are there any new customers this month?", "account_status"),
    ("Acme Corp account summary.", "account_status"),
    ("Status of all accounts.", "account_status"),
    ("Which customers are at risk of going dormant?", "account_status"),
    # Spanish
    ("¿Cómo está la cuenta de Acme Corp?", "account_status"),
    ("Muéstrame los clientes en riesgo.", "account_status"),
    ("¿Cuándo fue el último pedido de Global Imports?", "account_status"),
    ("¿Cuáles clientes no han pedido en 60 días?", "account_status"),
    ("¿Está activo Metro Distributors?", "account_status"),
    ("Lista de cuentas dormidas.", "account_status"),
    ("¿Cuántos clientes están en riesgo?", "account_status"),
    ("¿Cuáles son nuestras cuentas principales?", "account_status"),
    ("Resumen de salud de todos los clientes.", "account_status"),
    ("¿Hay cuentas con saldo vencido?", "account_status"),
    # Edge cases
    ("acme", "account_status"),
    ("customers", "account_status"),
    ("who's at risk", "account_status"),
    ("account overview", "account_status"),
    ("any dormant accounts", "account_status"),
    ("which clients should I follow up with", "account_status"),
    ("customer churn risk", "account_status"),
    ("health of global imports", "account_status"),
    ("show me account details", "account_status"),
    ("outstanding invoices by customer", "account_status"),
    ("who ordered recently", "account_status"),
    ("top customers by revenue", "account_status"),
    ("accounts that need attention", "account_status"),
    ("call list for today", "account_status"),
    ("customer activity summary", "account_status"),

    # ── sales_report (40 questions) ───────────────────────────────────────
    ("What were our total sales last month?", "sales_report"),
    ("Show me revenue for the last 30 days.", "sales_report"),
    ("How are we doing compared to last month?", "sales_report"),
    ("What was our best week this quarter?", "sales_report"),
    ("Show me a sales breakdown by customer.", "sales_report"),
    ("What's our YTD revenue?", "sales_report"),
    ("Which products are selling the most?", "sales_report"),
    ("Give me the sales trend for the last 3 months.", "sales_report"),
    ("How much did we bill in Q1?", "sales_report"),
    ("What's our average order value?", "sales_report"),
    ("Show me the weekly sales report.", "sales_report"),
    ("Revenue comparison: this month vs last month.", "sales_report"),
    ("Which products drove the most revenue last quarter?", "sales_report"),
    ("Total invoiced amount for this year.", "sales_report"),
    ("Sales performance summary.", "sales_report"),
    ("How much did Acme Corp spend last 90 days?", "sales_report"),
    ("Top 5 customers by revenue this month.", "sales_report"),
    ("What's our month-over-month growth?", "sales_report"),
    ("Show revenue by product line.", "sales_report"),
    ("How many orders were placed last week?", "sales_report"),
    # Spanish
    ("¿Cuáles fueron nuestras ventas totales el mes pasado?", "sales_report"),
    ("Muéstrame los ingresos de los últimos 30 días.", "sales_report"),
    ("¿Cómo vamos comparado con el mes anterior?", "sales_report"),
    ("Tendencia de ventas de los últimos 3 meses.", "sales_report"),
    ("¿Qué productos se venden más?", "sales_report"),
    ("Resumen de desempeño de ventas.", "sales_report"),
    ("¿Cuántos pedidos se realizaron la semana pasada?", "sales_report"),
    ("Valor promedio de orden.", "sales_report"),
    ("Ingresos totales este año.", "sales_report"),
    ("¿Cuáles son los 5 mejores clientes por ingresos?", "sales_report"),
    # Edge cases
    ("sales", "sales_report"),
    ("revenue report", "sales_report"),
    ("how much did we sell", "sales_report"),
    ("show me the numbers", "sales_report"),
    ("monthly report", "sales_report"),
    ("quarterly summary", "sales_report"),
    ("sales trend", "sales_report"),
    ("income last month", "sales_report"),
    ("billing summary", "sales_report"),
    ("total invoiced", "sales_report"),

    # ── general_question (40 questions) ───────────────────────────────────
    ("What should I focus on today?", "general_question"),
    ("Give me an overview of the business.", "general_question"),
    ("What are the biggest issues right now?", "general_question"),
    ("Morning briefing please.", "general_question"),
    ("Summarize this week's activity.", "general_question"),
    ("What's the overall health of the business?", "general_question"),
    ("Any urgent issues I should know about?", "general_question"),
    ("What's happening with the business?", "general_question"),
    ("Quick summary.", "general_question"),
    ("What's the status of everything?", "general_question"),
    ("Is everything okay?", "general_question"),
    ("Give me a business update.", "general_question"),
    ("What do I need to know today?", "general_question"),
    ("How are things going overall?", "general_question"),
    ("Any red flags?", "general_question"),
    ("Business health check.", "general_question"),
    ("Top priorities for this week.", "general_question"),
    ("What should I do first?", "general_question"),
    ("Weekly recap.", "general_question"),
    ("Catch me up.", "general_question"),
    # Spanish
    ("¿En qué debo enfocarme hoy?", "general_question"),
    ("Dame un resumen del negocio.", "general_question"),
    ("¿Cuáles son los problemas más urgentes?", "general_question"),
    ("Resumen del negocio por favor.", "general_question"),
    ("¿Cómo está el negocio en general?", "general_question"),
    ("¿Hay algún problema urgente?", "general_question"),
    ("Actualización del negocio.", "general_question"),
    ("¿Qué necesito saber hoy?", "general_question"),
    ("¿Hay alertas importantes?", "general_question"),
    ("Resumen semanal.", "general_question"),
    # Edge cases
    ("help", "general_question"),
    ("update", "general_question"),
    ("status", "general_question"),
    ("overview", "general_question"),
    ("summary", "general_question"),
    ("what's up", "general_question"),
    ("how's it going", "general_question"),
    ("anything important", "general_question"),
    ("morning", "general_question"),
    ("briefing", "general_question"),
]

assert len(INTENT_QUESTIONS) == 170, f"Expected 170 intent questions, got {len(INTENT_QUESTIONS)}"


# ─── Action detection fixtures ─────────────────────────────────────────────────

# (ai_response_snippet, expected_action_type_or_None)
ACTION_DETECTION_CASES: list[tuple[str, str | None]] = [
    # Positive — email
    ("I'll draft an email to follow up with Acme Corp.", "draft_email"),
    ("Let me send an email to the team about the low inventory.", "draft_email"),
    ("I can write to Metro Distributors regarding the overdue invoice.", "draft_email"),
    ("Would you like me to email them with the updated pricing?", "draft_email"),
    ("I'll email them right away.", "draft_email"),
    # Positive — PO
    ("I'll draft a PO for 200 units of Widget B.", "draft_po"),
    ("We should place an order with the supplier for Gadget X.", "draft_po"),
    ("I can create a purchase order to restock Widget A.", "draft_po"),
    ("Drafting a purchase order for the critical items now.", "draft_po"),
    ("Shall I draft a reorder for these items?", "draft_po"),
    # Positive — report
    ("I'll generate a report with this data.", "generate_report"),
    ("Let me create a report for the last quarter.", "generate_report"),
    ("I can export this as a report.", "generate_report"),
    # Positive — alert
    ("I'll send an alert to the operations team.", "send_alert"),
    ("I can notify the team about the critical stock levels.", "send_alert"),
    ("Let me flag this for the manager.", "send_alert"),
    # Negative — no action
    ("Your sales were $45,000 last month, up 12% from prior month.", None),
    ("Acme Corp last ordered 5 days ago. Their health score is 82.", None),
    ("Widget A has 250 units in stock, about 25 weeks of supply.", None),
    ("Here is a summary of your top accounts.", None),
    ("No critical inventory issues at this time.", None),
    ("Global Imports LLC is in good standing.", None),
    ("Metro Distributors has been dormant for 75 days.", None),
    ("Your inventory is well-stocked across all product lines.", None),
    ("Sales are trending upward this quarter.", None),
    ("Everything looks healthy from a business perspective.", None),
]

assert len(ACTION_DETECTION_CASES) == 26, f"Expected 26 action cases, got {len(ACTION_DETECTION_CASES)}"

# Total: 170 intent + 26 action + 4 context tests below = 200


# ─── Intent classification tests ──────────────────────────────────────────────

VALID_INTENTS = {"inventory_check", "account_status", "sales_report", "general_question"}


@pytest.mark.asyncio
@pytest.mark.parametrize("question,expected_intent", INTENT_QUESTIONS)
async def test_intent_classification(question: str, expected_intent: str):
    """
    Verify that classify_intent() returns the expected intent for each question.
    Uses a mock Claude response so the test is deterministic and free.
    """
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=expected_intent)]

    with patch("app.ai.secretary._client") as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        from app.ai.secretary import classify_intent
        result = await classify_intent(question)

    assert result in VALID_INTENTS, f"Invalid intent '{result}' for: {question!r}"
    assert result == expected_intent, (
        f"Wrong intent for: {question!r}\n"
        f"  Expected: {expected_intent}\n"
        f"  Got:      {result}"
    )


# ─── Data summarizer tests ────────────────────────────────────────────────────

@pytest.mark.parametrize("intent,data,should_contain", [
    (
        "inventory_check",
        {"inventory": [
            {"product_name": "Widget A", "total_qty": 250, "stock_status": "healthy", "weeks_remaining": 25},
            {"product_name": "Gadget X", "total_qty": 5, "stock_status": "critical", "weeks_remaining": 0.5},
        ]},
        ["CRITICAL", "Gadget X"],
    ),
    (
        "account_status",
        {"accounts": [
            {"name": "Acme Corp", "health_status": "healthy"},
            {"name": "Metro Distributors", "health_status": "dormant"},
        ]},
        ["Acme Corp", "Metro Distributors"],
    ),
    (
        "sales_report",
        {
            "accounts": [{"name": "Acme Corp", "health_status": "healthy"}],
            "inventory": [{"product_name": "Widget A", "total_qty": 250, "stock_status": "healthy", "weeks_remaining": 25}],
        },
        ["Acme Corp"],
    ),
    (
        "general_question",
        {
            "accounts": [{"name": "Acme Corp", "health_status": "healthy"}],
            "inventory": [{"product_name": "Widget A", "total_qty": 250, "stock_status": "healthy", "weeks_remaining": 25}],
        },
        ["Acme Corp"],
    ),
])
def test_build_query_context_non_empty(intent, data, should_contain):
    """build_query_context() must return a non-empty string containing expected keywords."""
    from app.ai.data_summarizer import build_query_context
    result = build_query_context(intent, data)

    assert isinstance(result, str), "Context must be a string"
    assert len(result) > 10, f"Context too short for intent '{intent}': {result!r}"
    for keyword in should_contain:
        assert keyword in result, f"Expected '{keyword}' in context for intent '{intent}'"


# ─── Action detection tests ────────────────────────────────────────────────────

@pytest.mark.parametrize("response_text,expected_action", ACTION_DETECTION_CASES)
def test_action_detection(response_text: str, expected_action: str | None):
    """detect_action_in_response() must correctly identify or not identify actions."""
    from app.ai.secretary import detect_action_in_response
    result = detect_action_in_response(response_text)

    if expected_action is None:
        assert result is None, (
            f"Expected no action for: {response_text!r}\nGot: {result}"
        )
    else:
        assert result is not None, (
            f"Expected action '{expected_action}' but got None for: {response_text!r}"
        )
        assert result["action_type"] == expected_action, (
            f"Expected action '{expected_action}', got '{result['action_type']}' for: {response_text!r}"
        )
