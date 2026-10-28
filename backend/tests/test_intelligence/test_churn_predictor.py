"""Deterministic retention-hypothesis fixtures; no account or provider calls."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.intelligence.churn_predictor import predict_churn_risk


TODAY = date(2026, 10, 26)


def order(identifier: str, days_ago: int, **updates) -> dict:
    result = {"id": identifier, "date": TODAY - timedelta(days=days_ago), "total": "100.00"}
    result.update(updates)
    return result


def predict(orders: list[dict], **options) -> dict:
    args = {"today": TODAY, "history_complete": True, "source_status": "fresh"}
    args.update(options)
    return predict_churn_risk("Fictional Account", orders, **args)


def test_default_unknown_until_complete_fresh_source_is_attested():
    history = [order("one", 80), order("two", 45)]
    unknown = predict_churn_risk("Fictional Account", history, today=TODAY)
    assert unknown["risk"] == "unknown"
    assert unknown["score"] is None
    assert unknown["evidence_status"] == "unavailable"


@pytest.mark.parametrize("source_status", ["stale", "partial", "unavailable"])
def test_stale_or_partial_feed_never_claims_churn_risk(source_status):
    result = predict([order("one", 80), order("two", 45)], source_status=source_status)
    assert result["risk"] == "unknown"
    assert result["score"] is None
    assert result["evidence_status"] == source_status


def test_non_exhaustive_history_never_claims_churn_risk():
    result = predict([order("one", 400), order("two", 200)], history_complete=False)
    assert result["risk"] == "unknown"
    assert result["score"] is None
    assert result["evidence_status"] == "incomplete_history"


def test_exhaustive_no_orders_is_not_customer_churn():
    result = predict([])
    assert result["risk"] == "not_applicable"
    assert result["score"] is None
    assert "no observed orders" in result["reason"].lower()


@pytest.mark.parametrize("history", [
    [order("once", 35)],
    [order("first", 35), order("second", 35)],
])
def test_one_order_or_one_day_does_not_establish_frequency(history):
    result = predict(history)
    assert result["risk"] == "unknown"
    assert result["score"] is None
    assert result["evidence_status"] == "insufficient_history"


def test_observed_frequent_orders_heuristic_low_risk_not_proof():
    history = [order(f"order-{i}", 70 - i * 10) for i in range(8)]
    result = predict(history)
    assert result["risk"] == "low"
    assert 0 <= result["score"] <= 1
    assert result["method"] == "rule_based_hypothesis"
    assert result["confidence"] == "not_calibrated"
    assert result["evidence_status"] == "complete_observed"


def test_observed_lapsed_orders_produce_explainable_high_risk_hypothesis():
    history = [order("one", 260), order("two", 240), order("three", 220)]
    result = predict(history)
    assert result["risk"] == "high"
    assert "220d" in result["reason"]
    assert result["method"] == "rule_based_hypothesis"


@pytest.mark.parametrize("bad_date", [
    None, "", "not-a-date", "2026-13-05", TODAY + timedelta(days=1),
    datetime(2026, 10, 26, 12, tzinfo=timezone.utc),
])
def test_corrupt_or_future_order_date_cannot_be_silently_dropped(bad_date):
    with pytest.raises(ValueError, match="date"):
        predict([order("one", 42), order("two", 30, date=bad_date)])


def test_repeated_verified_order_id_cannot_inflate_frequency():
    with pytest.raises(ValueError, match="duplicate"):
        predict([order("same", 80), order("same", 30)])


def test_missing_source_identity_is_unverifiable():
    with pytest.raises(ValueError, match="id"):
        predict([order("one", 80), {"date": TODAY - timedelta(days=30)}])


def test_mixed_valid_iso_and_date_values_are_sorted_deterministically():
    history = [order("new", 14), order("old", 35, date=(TODAY - timedelta(days=35)).isoformat()), order("mid", 21)]
    outcome = predict(history)
    assert outcome["risk"] in ("low", "medium", "high")
    assert outcome["score"] is not None


def test_unknown_source_status_rejected():
    with pytest.raises(ValueError, match="source_status"):
        predict([order("one", 80), order("two", 45)], source_status="optimistic")
