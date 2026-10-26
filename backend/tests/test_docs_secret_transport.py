"""Regression coverage: documentation credentials never transit through URLs."""

import base64
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import main


def test_docs_page_never_embeds_access_key_or_query_parameter():
    client = TestClient(main.app)
    key = "not-for-query-strings"
    with patch.object(main.settings, "docs_api_key", key):
        response = client.get("/docs", headers={"X-Docs-Key": key})

    assert response.status_code == 200
    assert key not in response.text
    assert "docs_key=" not in response.text
    assert "openapi.json" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_documentation_query_key_is_not_an_authentication_method():
    request = MagicMock()
    request.headers = {"x-docs-key": ""}
    request.query_params = {"docs_key": "my-docs-key"}
    with patch.object(main.settings, "docs_api_key", "my-docs-key"):
        assert main._docs_key_ok(request) is False


def test_docs_support_browser_basic_auth_and_reject_wrong_credentials():
    client = TestClient(main.app)
    with patch.object(main.settings, "docs_api_key", "my-docs-key"):
        good = client.get("/docs", auth=("docs", "my-docs-key"))
        bad = client.get("/docs", auth=("docs", "wrong"))
        no_key = client.get("/docs")
        schema = client.get("/api/docs/openapi.json", auth=("docs", "my-docs-key"))

    assert good.status_code == 200
    assert bad.status_code == 401
    assert no_key.status_code == 401
    assert no_key.headers["www-authenticate"] == 'Basic realm="SecretaryAI Docs"'
    assert schema.status_code == 200
    assert "paths" in schema.json()


def test_docs_rejects_malformed_basic_headers():
    request = MagicMock()
    request.headers = {"authorization": "Basic !!!invalid-base64!!!"}
    with patch.object(main.settings, "docs_api_key", "my-docs-key"):
        assert main._docs_key_ok(request) is False


def test_basic_docs_username_must_be_docs():
    key = base64.b64encode(b"other:my-docs-key").decode("ascii")
    request = MagicMock()
    request.headers = {"authorization": f"Basic {key}"}
    with patch.object(main.settings, "docs_api_key", "my-docs-key"):
        assert main._docs_key_ok(request) is False
