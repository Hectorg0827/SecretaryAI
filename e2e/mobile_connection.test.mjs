// Local Node 24 smoke test for the dependency-free React Native connection guard.
// Synthetic URLs only. No app, account, device, server or provider interaction.
import test from 'node:test';
import assert from 'node:assert/strict';
import { validatedApiBase } from '../mobile/src/api/connectionConfig.ts';

test('release accepts build-configured HTTPS and strips trailing slash', () => {
  assert.equal(validatedApiBase('https://staging.example.org/', false), 'https://staging.example.org');
});

test('release fails closed for missing, malformed, local or plain HTTP API hosts', () => {
  for (const url of ['', 'not a url', 'http://localhost:8000', 'https://localhost:8000',
    'http://provider.test', 'https://example.com', 'https://placeholder.test']) {
    assert.throws(() => validatedApiBase(url, false), Error, url);
  }
});

test('release does not allow URL userinfo, query or fragment', () => {
  for (const url of ['https://u:pass@api.example.org', 'https://api.example.org?token=abc',
    'https://api.example.org/#credential']) {
    assert.throws(() => validatedApiBase(url, false), Error, url);
  }
});

test('development allows loopback HTTP but no arbitrary insecure server', () => {
  assert.equal(validatedApiBase('http://127.0.0.1:8000/', true), 'http://127.0.0.1:8000');
  assert.equal(validatedApiBase('http://10.0.2.2:8000/', true), 'http://10.0.2.2:8000');
  assert.throws(() => validatedApiBase('http://unsafe-remote.test', true));
});
