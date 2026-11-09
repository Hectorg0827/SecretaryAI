// Shared helpers for the Lola acceptance suite (Playwright, no test runner needed).
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
exports.fakeJwt = (role) => `${b64({ alg: 'none', typ: 'JWT' })}.${b64({ role, sub: `demo-${role}`, company_id: 'demo', exp: 4102444800 })}.sig`;

/** Log in as a role by seeding the token, and stub the backend so nothing reaches a real server. */
exports.asRole = async (context, role, { outage } = {}) => {
  await context.addInitScript(([token, outage]) => {
    localStorage.setItem('secretary_token', token);
    if (outage) localStorage.setItem('lola_demo_outage', outage); else localStorage.removeItem('lola_demo_outage');
  }, [exports.fakeJwt(role), outage ?? '']);
  await context.route('http://localhost:8000/**', (route) => {
    const url = route.request().url();
    if (url.includes('/auth/me')) return route.fulfill({ json: { name: role === 'owner' ? 'Hector Garcia' : 'Demo User' } });
    if (url.includes('/api/agent/status')) return route.fulfill({ json: { connected: true, last_seen: null, agent_version: '1.0.6', platform: 'demo' } });
    return route.fulfill({ status: 503, json: { detail: 'offline in acceptance suite' } });
  });
};
