import assert from 'node:assert/strict';
export function validateIdentity(token, company, user, verifiedUser) {
  const parts = token.split('.');
  assert.equal(parts.length, 3);
  const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString());
  assert.equal(claims.company_id, company);
  assert.equal(claims.sub, user);
  assert.equal(claims.role, 'authenticated');
  assert.equal(verifiedUser.id, user);
}
export function reconcileUser(rows, email) {
  assert.ok(Array.isArray(rows));
  const matches = rows.filter(u => u.email === email);
  assert.ok(matches.length <= 1, 'Ambiguous fixture identity');
  return matches[0]?.id;
}
export function assertAbsent(rows) {
  assert.ok(Array.isArray(rows) && rows.length === 0, 'Residual fixtures');
}
