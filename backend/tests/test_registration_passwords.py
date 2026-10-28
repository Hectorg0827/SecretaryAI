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
    db = MagicMock()
    users = MagicMock()
    companies = MagicMock()
    db.table.side_effect = lambda name: users if name == 'users' else companies
    users.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
    companies.insert.return_value.execute.return_value = SimpleNamespace(data=[{'id': 'company-test'}])
    users.insert.return_value.execute.return_value = SimpleNamespace(data=[{'id': 'user-test'}])
    body = RegisterRequest(company_name='Staging Test', email='test@example.invalid', password='Strong-password17!')
    with patch('supabase.create_client', return_value=db), \
         patch('app.api.auth._seed_company_defaults', new_callable=AsyncMock), \
         patch('app.utils.audit.write_audit_event'):
        result = await register(body, _=None)
    assert result['role'] == 'owner'
    assert result['access_token']
    stored = users.insert.call_args.args[0]['hashed_password']
    assert 'password_hash' not in users.insert.call_args.args[0]
    assert verify_password(body.password, stored)
    assert not verify_password('Wrong-password17!', stored)


@pytest.mark.asyncio
async def test_hasher_failure_cannot_leave_an_orphan_company():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(data=[])
    body = RegisterRequest(company_name='Test', email='test@example.invalid', password='Strong-password17!')
    with patch('supabase.create_client', return_value=db), \
         patch('app.api.auth.hash_password', side_effect=RuntimeError('broken backend')):
        with pytest.raises(HTTPException) as err:
            await register(body, _=None)
    assert err.value.status_code == 503
    db.table.return_value.insert.assert_not_called()
