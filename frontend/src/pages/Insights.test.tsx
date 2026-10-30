import { afterEach, describe, expect, it } from 'vitest';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { Insights } from './Insights';
import { INSIGHTS, insightsForRole } from './insights/registry';
import type { Role } from '../hooks/useAuth';

// Roles the /insights route admits (App.tsx RoleGuard).
const ROUTE_ROLES: readonly Role[] = ['owner', 'manager', 'sales_rep', 'back_office'];

let root: Root | undefined;
let container: HTMLDivElement | undefined;

function setTestRole(role: string) {
  const payload = btoa(JSON.stringify({ sub: 'u1', company_id: 'c1', role }));
  localStorage.setItem('secretary_token', 'header.' + payload + '.signature');
}

async function render(path: string) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => {
    root?.render(
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/insights" element={<Insights />} />
          <Route path="/insights/:slug" element={<Insights />} />
        </Routes>
      </MemoryRouter>,
    );
  });
  return container;
}

afterEach(async () => {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  localStorage.clear();
});

describe('insight registry contract', () => {
  it('gives every module a unique URL-safe slug', () => {
    const slugs = INSIGHTS.map((mod) => mod.meta.slug);
    expect(new Set(slugs).size).toBe(slugs.length);
    for (const slug of slugs) expect(slug).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
  });

  it('only offers modules to roles the insights route admits', () => {
    for (const mod of INSIGHTS) {
      expect(mod.meta.roles.length, mod.meta.slug).toBeGreaterThan(0);
      for (const role of mod.meta.roles) expect(ROUTE_ROLES, mod.meta.slug).toContain(role);
      expect(mod.meta.title.trim(), mod.meta.slug).not.toBe('');
      expect(mod.meta.description.trim(), mod.meta.slug).not.toBe('');
    }
  });
});

describe('insights page', () => {
  it.each(ROUTE_ROLES)('lists exactly the modules allowed for %s', async (role) => {
    setTestRole(role);
    const page = await render('/insights');
    const links = [...page.querySelectorAll('a[href^="/insights/"]')].map((a) => a.getAttribute('href'));
    expect(links.sort()).toEqual(insightsForRole(role).map((mod) => `/insights/${mod.meta.slug}`).sort());
    expect(page.textContent).toContain('nothing here sends, orders or changes anything');
  });

  it('refuses an unknown or disallowed insight instead of rendering it', async () => {
    setTestRole('owner');
    const page = await render('/insights/not-a-real-insight');
    expect(page.textContent).toContain('This insight is not available for your role.');
  });

  it('does not render a module to a role outside its list', async () => {
    const restricted = INSIGHTS.find((mod) => ROUTE_ROLES.some((role) => !mod.meta.roles.includes(role)));
    if (!restricted) return;
    const outsider = ROUTE_ROLES.find((role) => !restricted.meta.roles.includes(role))!;
    setTestRole(outsider);
    const page = await render(`/insights/${restricted.meta.slug}`);
    expect(page.textContent).toContain('This insight is not available for your role.');
  });
});
