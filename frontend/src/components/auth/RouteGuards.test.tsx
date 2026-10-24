import { afterEach, describe, expect, it } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { PrivateRoute, RoleGuard } from './RouteGuards';

let root: Root | undefined;
let container: HTMLDivElement | undefined;

function setTestRole(role: string) {
  // Browser role parsing is for UI hints ONLY: no signed-token security
  // assertions can be made from a client-created token.
  const payload = btoa(JSON.stringify({ sub: 'u1', company_id: 'c1', role }));
  localStorage.setItem('secretary_token', 'header.' + payload + '.signature');
}

async function navigate(path: string) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/login" element={<div>Sign in</div>} />
          <Route path="/" element={<div>Dashboard</div>} />
          <Route path="/logistics" element={
            <PrivateRoute>
              <RoleGuard roles={['owner', 'manager']}>
                <div>Logistics</div>
              </RoleGuard>
            </PrivateRoute>
          } />
        </Routes>
      </MemoryRouter>,
    );
  });
  return container.textContent ?? '';
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  localStorage.clear();
});

describe('React Router 7 web authorization navigation', () => {
  it('sends an unauthenticated logistics visitor to sign-in', async () => {
    expect(await navigate('/logistics')).toContain('Sign in');
  });

  it('keeps authenticated owners on the protected page', async () => {
    setTestRole('owner');
    expect(await navigate('/logistics')).toContain('Logistics');
  });

  it('redirects an authenticated unauthorized role to dashboard', async () => {
    setTestRole('sales_rep');
    expect(await navigate('/logistics')).toContain('Dashboard');
  });

  it('never elevates an unknown role to an owner-only page', async () => {
    setTestRole('unknown_admin');
    expect(await navigate('/logistics')).toContain('Dashboard');
  });
});
