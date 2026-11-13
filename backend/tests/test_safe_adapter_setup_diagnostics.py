"""Regression coverage for credential-safe staging adapter diagnostics."""
import pytest

from tasks.base import SafeAdapterSetupError
from tasks.staging_snapshot_job import _safe_failure_code


@pytest.mark.parametrize("code", [
    "qbo_credential_decryption_failed",
    "microsoft_credential_decryption_failed",
    "google_credential_decryption_failed",
    "connector_configuration_invalid",
])
def test_safe_adapter_code_is_stable(code):
    assert _safe_failure_code(SafeAdapterSetupError(code)) == code


def test_safe_adapter_code_rejects_untrusted_text():
    with pytest.raises(ValueError):
        SafeAdapterSetupError("token=untrusted-provider-content")


def test_generic_value_error_never_exposes_exception_message():
    assert _safe_failure_code(ValueError("token=private")) == "source_validation_failed"
