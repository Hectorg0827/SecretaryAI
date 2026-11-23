#!/usr/bin/env node
// Isolated Supabase Auth/RLS acceptance test. Requires Node >=20.
// Run ONLY against an approved disposable staging branch with SUPABASE_URL,
// SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY and ALLOW_DISPOSABLE_AUTH_TESTS=yes.
// Creates temporary companies, users and accounts; cleans up in finally.
// Never prints secrets, tokens or customer data.
import assert from 'node:assert/strict';
import { randomUUID, randomBytes } from 'node:crypto';

const { SUPABASE_URL: base, SUPABASE_SERVICE_ROLE_KEY: service, SUPABASE_ANON_KEY: anon } = process.env;
assert.equal(process.env.ALLOW_DISPOSABLE_AUTH_TESTS, 'yes', 'Explicit disposable-test opt-in required');
assert.ok(base && service && anon, 'Missing Supabase environment configuration');
const host = new URL(base);
assert.equal(host.protocol, 'https:');
assert.ok(!['xerktvdgjvjozvckjyfp', 'gpeytsazslopipmplixe'].some(ref => host.hostname.includes(ref)),
  'Parent staging and production are forbidden');
assert.ok(process.env.EXPECTED_DISPOSABLE_PROJECT_REF && host.hostname.startsWith(process.env.EXPECTED_DISPOSABLE_PROJECT_REF + '.'),
  'URL must match an explicitly designated disposable branch');
const tag = randomUUID();
const password = randomBytes(32).toString('base64url') + 'Aa1!';
const companies = [], users = [], accounts = [];
async function request(path, key, method='GET', body, token=key) {
  const response = await fetch(base.replace(/\/$/,'') + path, {
    method, headers: { apikey:key, Authorization:'Bearer '+token, 'Content-Type':'application/json',
      Prefer:'return=representation' },
    ...(body === undefined ? {} : {body:JSON.stringify(body)})
  });
  const raw = await response.text();
  let data; try { data = JSON.parse(raw); } catch { data = raw; }
  return {status:response.status, ok:response.ok, data};
}
async function must(path,key,method,body,token) {
  const result=await request(path,key,method,body,token);
  assert.ok(result.ok, method+' '+path+' returned HTTP '+result.status);
  return result.data;
}
async function makeCompany(letter) {
  const rows=await must('/rest/v1/companies',service,'POST',{name:'RLS-ACCEPTANCE-'+letter+'-'+tag});
  const id=rows[0].id; companies.push(id); return id;
}
async function makeUser(letter, companyId) {
  const email='rls-'+tag+'-'+letter+'@example.invalid';
  const result=await must('/auth/v1/admin/users',service,'POST',{
    email,password,email_confirm:true,app_metadata:{company_id:companyId}
  });
  users.push(result.id); return email;
}
async function login(email) {
  const result=await must('/auth/v1/token?grant_type=password',anon,'POST',{email,password});
  assert.ok(result.access_token); return result.access_token;
}
try {
  const a=await makeCompany('A'), b=await makeCompany('B');
  const ea=await makeUser('a',a), eb=await makeUser('b',b);
  const ta=await login(ea), tb=await login(eb);
  // This acceptance test requires a verified trusted JWT company_id hook.
  // App metadata alone does NOT automatically create a top-level JWT claim.
  const jwt=JSON.parse(Buffer.from(ta.split('.')[1],'base64url').toString());
  assert.equal(jwt.company_id,a,'Trusted JWT hook must issue the expected company_id claim');
  const rows=await must('/rest/v1/accounts',service,'POST',{company_id:a,name:'RLS-ACCEPTANCE-'+tag});
  const account=rows[0].id; accounts.push(account);
  const own=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon,'GET',undefined,ta);
  assert.ok(own.ok && own.data.length===1,'Company A must read its own account');
  const other=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon,'GET',undefined,tb);
  assert.ok(other.ok && other.data.length===0,'Company B must not read A');
  const anonymous=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon);
  assert.ok(anonymous.ok && anonymous.data.length===0,'Anonymous client must not read A');
  const reassignment=await request('/rest/v1/accounts?id=eq.'+account,anon,'PATCH',{company_id:b},ta);
  assert.ok(!reassignment.ok || reassignment.data.length===0,'Tenant reassignment must be rejected');
  const crossDelete=await request('/rest/v1/accounts?id=eq.'+account,anon,'DELETE',undefined,tb);
  assert.ok(!crossDelete.ok || crossDelete.data.length===0,'Cross-tenant DELETE must be rejected');
  console.log('PASS: real Auth sessions, own read, foreign read, anonymous read, reassignment, foreign delete');
} finally {
  for(const id of accounts) await request('/rest/v1/accounts?id=eq.'+id,service,'DELETE');
  for(const id of users) await request('/auth/v1/admin/users/'+id,service,'DELETE');
  for(const id of companies) await request('/rest/v1/companies?id=eq.'+id,service,'DELETE');
  console.log('Cleanup attempted for all created fixtures; verify disposable branch before deletion.');
}
