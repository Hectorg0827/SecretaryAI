import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { api, describeApproval } from './api';
import { setSessionMode } from './session';

beforeEach(() => setSessionMode('token'));
import { passwordProblem } from '../pages/AccountLinks';

function jsonResponse(status: number, body: unknown): Response {
  return { status, ok: status >= 200 && status < 300, json: async () => body } as unknown as Response;
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  sessionStorage.clear();
});

describe('approval result is reported honestly', () => {
  it('a failed action is not success and stays in the queue', () => {
    const r = describeApproval({ draft_id: 'd', status: 'pending', execution_status: 'failed', execution_detail: 'The email could not be sent: mailbox not connected' });
    expect(r.ok).toBe(false);
    expect(r.done).toBe(false);
    expect(r.message).toContain('could not be sent');
  });

  it('a manual step is explained, not called "sent"', () => {
    const r = describeApproval({ draft_id: 'd', status: 'approved', execution_status: 'manual_required', execution_detail: 'Approved. … NOT created automatically' });
    expect(r.ok).toBe(true);
    expect(r.done).toBe(true);
    expect(r.message).toContain('NOT created');
  });

  it('an executed action shows what happened', () => {
    expect(describeApproval({ draft_id: 'd', status: 'approved', execution_status: 'executed', execution_detail: 'Email sent to a@b.test' }).message)
      .toBe('Email sent to a@b.test');
  });

  it('a second click on something already handled is not an error', () => {
    const r = describeApproval({ draft_id: 'd', status: 'approved', already_processed: true });
    expect(r.ok && r.done).toBe(true);
  });

  it('edits are sent in the shape the server reads', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { draft_id: 'd', status: 'edited_and_approved' }));
    vi.stubGlobal('fetch', fetchMock);
    await api.actions.approve('d', { to: 'x@y.test' });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ edited_content: { to: 'x@y.test' } });
  });
});

describe('sign-in errors are shown, not treated as an expired session', () => {
  it('wrong password: no refresh attempt, message from the server', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(401, { detail: 'Invalid email or password' }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(api.auth.login('a@b.test', 'nope')).rejects.toThrow('Invalid email or password');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('validation errors use the plain sentence from the server', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(422, {
      error: 'validation_error', message: 'Password must be at least 8 characters',
      detail: [{ loc: ['body', 'new_password'], msg: 'Password must be at least 8 characters', type: 'value_error' }],
    })));
    await expect(api.auth.resetPassword('t', 'short')).rejects.toThrow('Password must be at least 8 characters');
  });

  it('an expired trial gets a clear message', async () => {
    localStorage.setItem('secretary_token', 't');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(402, {})));
    await expect(api.get('/api/feed/')).rejects.toThrow(/trial or subscription has ended/);
  });
});

describe('password rules match the server', () => {
  it.each([
    ['short1', 'short1', 'Use at least 8 characters.'],
    ['12345678', '12345678', 'Include at least one letter.'],
    ['abcdefgh', 'abcdefgh', 'Include at least one number.'],
    ['Abcdefg1', 'Abcdefg2', 'The two passwords do not match.'],
  ])('%s is rejected', (pw, confirm, problem) => {
    expect(passwordProblem(pw, confirm)).toBe(problem);
  });

  it('a good password passes', () => {
    expect(passwordProblem('Abcdefg1', 'Abcdefg1')).toBeNull();
  });
});

describe('two tabs refreshing at once', () => {
  it('uses the token the other tab already stored instead of signing out', async () => {
    localStorage.setItem('secretary_token', 'old-token');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      // our refresh loses the race: the other tab used the token first …
      .mockImplementationOnce(async () => {
        localStorage.setItem('secretary_token', 'token-from-other-tab');
        return jsonResponse(401, { detail: 'Token already used; please log in again' });
      })
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api.get<{ ok: boolean }>('/api/dashboard/summary')).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls[2][1].headers.Authorization).toBe('Bearer token-from-other-tab');
    expect(localStorage.getItem('secretary_token')).toBe('token-from-other-tab');
  });
});

describe('an unknown outcome', () => {
  it('is not shown as success, and leaves the queue so it cannot be sent twice', () => {
    const r = describeApproval({ draft_id: 'd', status: 'approved', execution_status: 'unknown', execution_detail: 'We could not confirm whether the email to a@b.test was sent. Check your Sent folder before sending it again.' });
    expect(r.ok).toBe(false);
    expect(r.done).toBe(true);
    expect(r.message).toContain('Sent folder');
    expect(r.message).toContain('Needs checking');
  });
});

describe('cookie sessions (the token never reaches page scripts)', () => {
  beforeEach(() => setSessionMode('cookie'));

  it('sign-in stores no token — only non-secret flags and the CSRF value', async () => {
    const { saveSession, isSignedIn, getToken, currentRole } = await import('./session');
    saveSession({ csrf_token: 'csrf-1', role: 'manager', company_id: 'c1', access_token: 'SHOULD-NOT-BE-KEPT' });
    expect(isSignedIn()).toBe(true);
    expect(getToken()).toBeNull();
    expect(currentRole()).toBe('manager');
    expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain('SHOULD-NOT-BE-KEPT');
  });

  it('requests send the cookie and the CSRF header, and no Authorization header', async () => {
    const { saveSession } = await import('./session');
    saveSession({ csrf_token: 'csrf-1', role: 'owner', company_id: 'c1' });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await api.post('/api/users/invite', { email: 'a@b.test', role: 'viewer' });
    const init = fetchMock.mock.calls[0][1];
    expect(init.credentials).toBe('include');
    expect(init.headers['X-CSRF-Token']).toBe('csrf-1');
    expect(init.headers['X-Session-Mode']).toBe('cookie');
    expect(init.headers.Authorization).toBeUndefined();
  });

  it('reads do not send the CSRF value', async () => {
    const { saveSession } = await import('./session');
    saveSession({ csrf_token: 'csrf-1', role: 'owner', company_id: 'c1' });
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { users: [], roles: [] }));
    vi.stubGlobal('fetch', fetchMock);
    await api.users.list();
    expect(fetchMock.mock.calls[0][1].headers['X-CSRF-Token']).toBeUndefined();
  });

  it('an expired session is refreshed through the cookie, with no token in the body', async () => {
    const { saveSession, getCsrf } = await import('./session');
    saveSession({ csrf_token: 'csrf-1', role: 'owner', company_id: 'c1' });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(401, { detail: 'expired' }))
      .mockResolvedValueOnce(jsonResponse(200, { session: 'cookie', csrf_token: 'csrf-2', role: 'owner', company_id: 'c1' }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(api.post('/api/x', { a: 1 })).resolves.toEqual({ ok: true });
    expect(fetchMock.mock.calls[1][0]).toContain('/auth/refresh');
    expect(fetchMock.mock.calls[1][1].body).toBe('{}');
    expect(getCsrf()).toBe('csrf-2');
    expect(fetchMock.mock.calls[2][1].headers['X-CSRF-Token']).toBe('csrf-2');
  });

  it('a new tab fetches its CSRF value before changing anything', async () => {
    localStorage.setItem('secretary_signed_in', '1');   // signed in elsewhere; this tab has no CSRF yet
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(200, { role: 'owner', company_id: 'c1', csrf_token: 'csrf-9' }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: true }));
    vi.stubGlobal('fetch', fetchMock);
    await api.post('/api/x', {});
    expect(fetchMock.mock.calls[0][0]).toContain('/auth/session');
    expect(fetchMock.mock.calls[1][1].headers['X-CSRF-Token']).toBe('csrf-9');
  });
});
