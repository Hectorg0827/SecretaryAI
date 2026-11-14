"""Contract tests for the live, credential-free staging boundary probe."""
from scripts.staging_public_auth_smoke import PROTECTED, verify


def test_healthy_backend_and_unauthenticated_endpoints_pass():
    calls = []
    def request(path):
        calls.append(path)
        return 200 if path == "/health" else 401
    result = verify(request)
    assert result["result"] == "PASS_PUBLIC_AUTH_ONLY"
    assert result["business_acceptance"] is False
    assert result["authentication"] == "no credentials sent"
    assert set(calls) == {"/health", *PROTECTED}


def test_exposed_price_book_fails_audit():
    def request(path):
        if path == "/health":
            return 200
        if path == "/api/insights/price-book":
            return 200
        return 401
    result = verify(request)
    assert result["result"] == "FAILED_PUBLIC_AUTH_BOUNDARY"
    assert any(not item["passed"] for item in result["checks"])


def test_unexpected_404_and_503_are_not_counted_as_auth_success():
    for broken in (404, 503, 302):
        def request(path):
            return 200 if path == "/health" else broken
        result = verify(request)
        assert result["result"] == "FAILED_PUBLIC_AUTH_BOUNDARY"


def test_health_outage_fails_even_if_roles_still_deny():
    result = verify(lambda path: 503 if path == "/health" else 401)
    assert result["result"] == "FAILED_PUBLIC_AUTH_BOUNDARY"
