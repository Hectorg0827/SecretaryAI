/**
 * Shared helpers for the logistics component tests. Every API call is mocked
 * and global fetch is stubbed to fail, so no test can reach a real backend.
 */
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { vi } from 'vitest';
import { api } from '../../lib/api';

type RouteValue = unknown | Error | (() => unknown);
export type RouteTable = Record<string, RouteValue>;

let root: Root | undefined;
let container: HTMLDivElement | undefined;

export function setRole(role: string, sub = 'user-1', companyId = 'company-1') {
  const payload = btoa(JSON.stringify({ sub, company_id: companyId, role }));
  localStorage.setItem('secretary_token', `header.${payload}.signature`);
}

export async function render(element: React.ReactElement): Promise<HTMLDivElement> {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  await act(async () => { root?.render(element); });
  await flush();
  return container;
}

export async function cleanup() {
  if (root) await act(async () => root?.unmount());
  root = undefined;
  container?.remove();
  container = undefined;
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
}

function resolveRoute(table: RouteTable, method: string, path: string): unknown {
  if (!(path in table)) throw new Error(`Unexpected ${method} ${path}`);
  const value = table[path];
  if (value instanceof Error) throw value;
  return typeof value === 'function' ? (value as () => unknown)() : value;
}

/** Mock api.get/api.post by exact path. Unknown paths reject, like a failed request. */
export function mockApi(get: RouteTable = {}, post: RouteTable = {}) {
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('Network disabled in tests'))));
  const getSpy = vi.spyOn(api, 'get').mockImplementation(
    (async (path: string) => resolveRoute(get, 'GET', path)) as typeof api.get);
  const postSpy = vi.spyOn(api, 'post').mockImplementation(
    (async (path: string) => resolveRoute(post, 'POST', path)) as typeof api.post);
  return { get: getSpy, post: postSpy, getRoutes: get, postRoutes: post };
}

export async function flush() {
  for (let i = 0; i < 5; i += 1) {
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
  }
}

export function button(scope: ParentNode, text: string): HTMLButtonElement | undefined {
  return Array.from(scope.querySelectorAll('button')).find((item) => item.textContent?.trim() === text);
}

export async function click(element: Element | null | undefined) {
  if (!element) throw new Error('Element to click was not found');
  await act(async () => { (element as HTMLElement).click(); });
  await flush();
}

type Editable = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

export async function type(element: Element | null | undefined, value: string) {
  if (!element) throw new Error('Field to fill was not found');
  const target = element as Editable;
  const prototype = Object.getPrototypeOf(target);
  const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
  await act(async () => {
    setter?.call(target, value);
    target.dispatchEvent(new Event(target instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }));
  });
}

export async function check(element: Element | null | undefined) {
  if (!element) throw new Error('Checkbox was not found');
  await act(async () => { (element as HTMLInputElement).click(); });
}

export function byLabel(scope: ParentNode, label: string): Editable | null {
  return scope.querySelector(`[aria-label="${label}"]`) as Editable | null;
}

export function dialog(scope: ParentNode): HTMLElement | null {
  return scope.querySelector('[role="alertdialog"]');
}

export async function confirm(scope: ParentNode) {
  await click(scope.querySelector('[data-testid="confirm-write"]'));
}

/** The form control a visible <label> points at, matched by its exact text. */
export function field(scope: ParentNode, labelText: string): Editable | null {
  const label = Array.from(scope.querySelectorAll('label')).find((item) => item.textContent?.trim() === labelText);
  const id = label?.getAttribute('for');
  return id ? (document.getElementById(id) as Editable | null) : null;
}
