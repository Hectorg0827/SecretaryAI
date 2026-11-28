import test from 'node:test';
import assert from 'node:assert/strict';
import {deleteAndVerifyFixture, cleanupFixtures} from './rls_fixture_cleanup.mjs';

const id = 'fixture-1', path = '/rest/v1/accounts?id=eq.fixture-1&select=id';
test('confirmed deletion and no residual rows', async () => {
  const calls = [];
  await deleteAndVerifyFixture({kind:'account', id, path, request:async (_, method) => {
    calls.push(method);
    return {ok:true,status:200,data:method==='DELETE'?[{id}]:[]};
  }});
  assert.deepEqual(calls,['DELETE','GET']);
});
test('zero-row successful DELETE is rejected', async () => {
  await assert.rejects(deleteAndVerifyFixture({kind:'account',id,path,request:async()=>({ok:true,status:200,data:[]})}), /expected ID/);
});
test('unexpected deleted ID is rejected', async () => {
  await assert.rejects(deleteAndVerifyFixture({kind:'account',id,path,request:async()=>({ok:true,status:200,data:[{id:'other'}]})}), /expected ID/);
});
test('incomplete cleanup is rejected after successful DELETE', async () => {
  await assert.rejects(deleteAndVerifyFixture({kind:'account',id,path,request:async(_,method)=>({ok:true,status:200,data:[{id}]})}), /remains/);
});
test('cleanup continues after failure and reports it', async () => {
  const seen=[];
  const failures=await cleanupFixtures({accounts:['a','b'],users:[],companies:[],request:async(p,method)=>{
    seen.push([p,method]);
    return {ok:true,status:200,data:method==='DELETE'?(p.includes('eq.a')?[]:[{id:'b'}]):[]};
  }});
  assert.equal(failures.length,1);
  assert.match(failures[0],/account:a/);
  assert.equal(seen.filter(([p,m])=>p.includes('eq.b')&&m==='GET').length,1);
});
test('failed verification GET is reported', async () => {
  const failures=await cleanupFixtures({accounts:['a'],users:[],companies:[],request:async(_,method)=>method==='DELETE'?{ok:true,status:200,data:[{id:'a'}]}:{ok:false,status:503,data:null}});
  assert.equal(failures.length,1);
  assert.match(failures[0],/verification GET/);
});
