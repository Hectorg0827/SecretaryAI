import type { Role } from '../../hooks/useAuth';

const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
export function loginAs(role: Role) {
  localStorage.setItem('secretary_token', `${b64({ alg: 'none' })}.${b64({ role, sub: 'u1' })}.sig`);
}

export function sseResponse(lines: string[], status = 200): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) { for (const l of lines) c.enqueue(enc.encode(l)); c.close(); },
  });
  return new Response(body, { status, headers: { 'Content-Type': 'text/event-stream' } });
}

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}
