#!/usr/bin/env node
// Isolated Supabase Auth/RLS acceptance test. Requires Node >=20.
// Run ONLY against an approved disposable staging branch with SUPABASE_URL,
// SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY and ALLOW_DISPOSABLE_AUTH_TESTS=yes.
// Creates temporary companies, users and accounts; cleans up in finally.
// Never prints secrets, tokens or customer data.
import assert from 'node:assert/strict';
import { randomUUID, randomBytes } from 'node:crypto';
import { cleanupFixtures } from './rls_fixture_cleanup.mjs';

const { SUPABASE_URL: base, SUPABASE_SERVICE_ROLE_KEY: service, SUPABASE_ANON_KEY: anon } = process.env;
assert.equal(process.env.ALLOW_DISPOSABLE_AUTH_TESTS, 'yes', 'Explicit disposable-test opt-in required');
assert.ok(base && service && anon, 'Missing Supabase environment configuration');
const host = new URL(base);
assert.equal(host.protocol, 'https:');
assert.ok(!['xerktvdgjvjozvckjyfp', 'gpeytsazslopipmplixe', 'toujmmvlgktprabjbccq', 'djfcginvavwuyfxxlyjv', 'keopiqwmcrftydjvztev'].some(ref => host.hostname.includes(ref)),
  'Parent staging and production are forbidden');
assert.ok(process.env.EXPECTED_DISPOSABLE_PROJECT_REF && host.hostname.startsWith(process.env.EXPECTED_DISPOSABLE_PROJECT_REF + '.'),
  'URL must match an explicitly designated disposable branch');
const tag = randomUUID();
const foreignId=randomUUID();
const password = randomBytes(32).toString('base64url') + 'Aa1!';
const companies = [], users = [], accounts = [], emails = [];
import { validateIdentity, reconcileUser, assertAbsent } from './rls_identity.mjs';
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
  const id=randomUUID(); companies.push(id);
  const rows=await must('/rest/v1/companies',service,'POST',{id,name:'RLS-ACCEPTANCE-'+letter+'-'+tag});
  assert.equal(rows[0].id,id); return id;
}
async function makeUser(letter, companyId) {
  const email='rls-'+tag+'-'+letter+'@example.invalid';
  emails.push(email);
  const result=await must('/auth/v1/admin/users',service,'POST',{
    email,password,email_confirm:true,app_metadata:{company_id:companyId}
  });
  assert.ok(result.id); users.push(result.id); return {email,id:result.id};
}
async function login(email) {
  const result=await must('/auth/v1/token?grant_type=password',anon,'POST',{email,password});
  assert.ok(result.access_token); return result.access_token;
}
try {
  const a=await makeCompany('A'), b=await makeCompany('B');
  const ea=await makeUser('a',a), eb=await makeUser('b',b);
  const ta=await login(ea.email), tb=await login(eb.email);
  assert.notEqual(ea.id,eb.id);
  validateIdentity(ta,a,ea.id,await must('/auth/v1/user',anon,'GET',undefined,ta));
  validateIdentity(tb,b,eb.id,await must('/auth/v1/user',anon,'GET',undefined,tb));
  for (const [company,token] of [[a,ta],[b,tb]]) {
    const id=randomUUID(); accounts.push(id);
    const p='/rest/v1/accounts?id=eq.'+id+'&select=id,company_id';
    const created=await must('/rest/v1/accounts',anon,'POST',{id,company_id:company,name:'RLS-CONTROL-'+tag},token);
    assert.equal(created[0].id,id);
    assert.equal((await must(p,anon,'GET',undefined,token))[0].company_id,company);
    assert.equal((await must(p,anon,'PATCH',{name:'RLS-CONTROL-UPDATED-'+tag},token))[0].id,id);
  }
  // This acceptance test requires a verified trusted JWT company_id hook.
  // App metadata alone does NOT automatically create a top-level JWT claim.
  const jwt=JSON.parse(Buffer.from(ta.split('.')[1],'base64url').toString());
  assert.equal(jwt.company_id,a,'Trusted JWT hook must issue the expected company_id claim');
  const account=randomUUID(); accounts.push(account);
  const rows=await must('/rest/v1/accounts',service,'POST',{id:account,company_id:a,name:'RLS-ACCEPTANCE-'+tag});
  assert.equal(rows[0].id,account);
  const own=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon,'GET',undefined,ta);
  assert.ok(own.ok && own.data.length===1,'Company A must read its own account');
  const other=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon,'GET',undefined,tb);
  assert.ok(other.ok && other.data.length===0,'Company B must not read A');
  const anonymous=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon);
  assert.ok(anonymous.ok && anonymous.data.length===0,'Anonymous client must not read A');
  const ownId=randomUUID(); accounts.push(ownId);
  const ownInsert=await request('/rest/v1/accounts',anon,'POST',{id:ownId,company_id:a,name:'RLS-OWN-'+tag},ta);
  assert.ok(ownInsert.ok && Array.isArray(ownInsert.data) && ownInsert.data.length===1,
    'Company A must be able to create its own account');
  assert.equal(ownInsert.data[0].id,ownId);
  
  const foreignInsert=await request('/rest/v1/accounts',anon,'POST',{id:foreignId,company_id:b,name:'RLS-FOREIGN-'+tag},ta);
  if (foreignInsert.ok && Array.isArray(foreignInsert.data)) {
    for (const row of foreignInsert.data) if (row.id) accounts.push(row.id);
  }
  assert.ok(!foreignInsert.ok || (Array.isArray(foreignInsert.data) && foreignInsert.data.length===0),
    'Company A must not create an account in Company B');
  const ownUpdate=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon,'PATCH',
    {name:'RLS-UPDATED-'+tag},ta);
  assert.ok(ownUpdate.ok && Array.isArray(ownUpdate.data) && ownUpdate.data.length===1,
    'Company A must be able to update its own account');
  const reassignment=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon,'PATCH',{company_id:b},ta);
  assert.ok(!reassignment.ok || reassignment.data.length===0,'Tenant reassignment must be rejected');
  const crossDelete=await request('/rest/v1/accounts?id=eq.'+account+'&select=id',anon,'DELETE',undefined,tb);
  assert.ok(!crossDelete.ok || crossDelete.data.length===0,'Cross-tenant DELETE must be rejected');
  const after=await must('/rest/v1/accounts?id=eq.'+account+'&select=id,company_id',service,'GET');
  assert.equal(after.length,1,'Protected record must still exist after forbidden operations');
  assert.equal(after[0].company_id,a,'Protected record must retain original company ownership');
  assert.equal((await must('/rest/v1/accounts?name=eq.RLS-FOREIGN-'+tag+'&select=id',service,'GET')).length,0,
    'Foreign tenant insert must not persist any record');
  console.log('PASS: real Auth sessions, own read, foreign read, anonymous read, reassignment, foreign delete, own insert/update, foreign insert, persisted ownership');
} finally {
  const failures = [];
  try {
    for (let page=1; ; page++) {
      const result=await must('/auth/v1/admin/users?page='+page+'&per_page=100',service,'GET');
      assert.ok(Array.isArray(result.users));
      for (const email of emails) {
        const id=reconcileUser(result.users,email);
        if(id && !users.includes(id)) users.push(id);
      }
      if(result.users.length<100) break;
      assert.ok(page<1000,'User reconciliation pagination limit');
    }
    for (const [table,ids] of [['accounts',accounts],['companies',companies]]) {
      const existing=await must('/rest/v1/'+table+'?select=id&name=like.*'+tag+'*',service,'GET');
      for(const row of existing) if(!ids.includes(row.id)) ids.push(row.id);
      for(let i=ids.length-1;i>=0;i--) {
        const found=await must('/rest/v1/'+table+'?select=id&id=eq.'+ids[i],service,'GET');
        if(found.length===0) ids.splice(i,1);
      }
    }
  } catch { failures.push('reconciliation:failed'); }
  failures.push(...await cleanupFixtures({
    accounts, users, companies,
    request: (path, method) => request(path, service, method)
  }));
  try {
    for(const table of ['accounts','companies']) assertAbsent(await must('/rest/v1/'+table+'?select=id&name=like.*'+tag+'*',service,'GET'));
    assertAbsent(await must('/rest/v1/accounts?select=id&id=eq.'+foreignId,service,'GET'));
  } catch { failures.push('absence:failed'); }
  if (failures.length) {
    console.error('Fixture cleanup verification failed (' + failures.length + '): ' +
      failures.map(f => f.split(':')[0]).join(',') + '; disposable branch must be deleted.');
    process.exitCode = 1;
  } else console.log('PASS: fixture IDs deleted and absence verified; delete disposable branch after verification.');
}
