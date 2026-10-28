import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ROLE_PERMISSIONS } from './useAuth';

// UI gating must not drift from the server's permission map.
function backendPermissions(): Record<string, string[]> {
  // vitest runs from frontend/ (npm test and CI).
  const path = resolve(process.cwd(), '../backend/app/auth/rbac.py');
  const source = readFileSync(path, 'utf8');
  const block = source.slice(source.indexOf('ROLE_PERMISSIONS'), source.indexOf('\n}\n'));
  const roles: Record<string, string[]> = {};
  for (const match of block.matchAll(/"(\w+)":\s*\{([^}]*)\}/g)) {
    roles[match[1]] = [...match[2].matchAll(/"(\w+)"/g)].map((perm) => perm[1]).sort();
  }
  return roles;
}

describe('web permission map', () => {
  it('mirrors backend/app/auth/rbac.py exactly', () => {
    const web = Object.fromEntries(
      Object.entries(ROLE_PERMISSIONS).map(([role, perms]) => [role, [...perms].sort()]),
    );
    const backend = backendPermissions();
    expect(Object.keys(backend).length).toBeGreaterThan(0);
    expect(web).toEqual(backend);
  });
});
