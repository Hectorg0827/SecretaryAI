"""Registration must exercise the real password hasher; no availability skips."""
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.auth import RegisterRequest, register
from app.auth.jwt import verify_password


@pytest.mark.parametrize('password', ['é' * 36 + 'A1', 'Valid1\x00password', 'A1' + 'x' * 71])
def test_registration_rejects_unsupported_password_before_database(password):
    with pytest.raises(ValidationError):
        RegisterRequest(company_name='Test', email='test@example.invalid', password=password)


@pytest.mark.asyncio
async def test_registration_hashes_and_verifies_the_actual_submitted_password():
    captured = {}

    async def atomic_writer(**kwargs):
        captured.update(kwargs)
        return {"id": "user-test", "company_id": "company-test"}

    body = RegisterRequest(company_name='Staging Test', email='test@example.invalid',
                           password='Strong-password17!')
    with (patch('app.auth.registration.create_company_owner_transactionally',
                new=atomic_writer),
          patch('supabase.create_client', return_value=MagicMock()),
          patch('app.utils.audit.write_audit_event')):
        result = await register(body, _=None)

    assert result['role'] == 'owner'
    assert result['access_token']
    stored = captured['hashed_password']
    assert 'password_hash' not in captured
    assert verify_password(body.password, stored)
    assert not verify_password('Wrong-password17!', stored)


@pytest.mark.asyncio
async def test_hasher_failure_cannot_leave_an_orphan_company():
    body = RegisterRequest(company_name='Test', email='test@example.invalid',
                           password='Strong-password17!')
    with (patch('app.auth.registration.create_company_owner_transactionally',
                new_callable=AsyncMock) as atomic_writer,
          patch('app.api.auth.hash_password',
                side_effect=RuntimeError('broken backend')),
          patch('supabase.create_client') as client):
        with pytest.raises(HTTPException) as err:
            await register(body, _=None)

    assert err.value.status_code == 503
    atomic_writer.assert_not_awaited()
    client.assert_not_called()
