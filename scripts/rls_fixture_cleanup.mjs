// Provider-free fixture cleanup verification. Fail closed on zero-row deletes and leftovers.
import assert from 'node:assert/strict';

export async function deleteAndVerifyFixture({request, path, id, kind}) {
  const deleted = await request(path, 'DELETE');
  assert.ok(deleted.ok, kind + ' DELETE HTTP ' + deleted.status);
  assert.ok(Array.isArray(deleted.data), kind + ' DELETE must return representation');
  assert.deepEqual(deleted.data.map(row => row.id), [id], kind + ' DELETE must return exactly the expected ID');
  const remaining = await request(path, 'GET');
  assert.ok(remaining.ok, kind + ' verification GET HTTP ' + remaining.status);
  assert.ok(Array.isArray(remaining.data) && remaining.data.length === 0,
    kind + ' fixture remains after DELETE');
}

export async function cleanupFixtures({request, accounts, users, companies}) {
  const failures = [];
  for (const [kind, ids, makePath] of [
    ['account', accounts, id => '/rest/v1/accounts?id=eq.' + id + '&select=id'],
    ['user', users, id => '/auth/v1/admin/users/' + id],
    ['company', companies, id => '/rest/v1/companies?id=eq.' + id + '&select=id']
  ]) {
    for (const id of ids) {
      try {
        if (kind === 'user') {
          const deleted = await request(makePath(id), 'DELETE');
          assert.ok(deleted.ok, 'user DELETE HTTP ' + deleted.status);
          const remaining = await request(makePath(id), 'GET');
          assert.equal(remaining.status, 404, 'user must be absent after DELETE');
        } else {
          await deleteAndVerifyFixture({request, path:makePath(id), id, kind});
        }
      } catch (error) { failures.push(kind + ':' + id + ': ' + error.message); }
    }
  }
  return failures;
}
