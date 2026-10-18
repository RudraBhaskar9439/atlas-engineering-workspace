import {test} from 'node:test';
import assert from 'node:assert/strict';
import {publicationReadiness} from '../src/publication.js';
const run={patch_hash:'reviewed',ci:{head_sha:'commit'},validation:{kind:'github_ci',status:'failed',patch_hash:'reviewed',head_sha:'commit',destination:'owner/fork'}};
test('failed native checks allow an explicitly unverified draft without claiming success',()=>{
 assert.deepEqual(publicationReadiness(run,'Owner/Fork'),{checksPassed:false,unverified:true,canPublish:true});
});
test('pending and absent workflow runs remain unverified',()=>{
 for(const status of ['pending','not_started'])assert.equal(publicationReadiness({...run,validation:{...run.validation,status}},'owner/fork').unverified,true);
});
test('stale evidence, another head, another fork, and missing checks cannot enable publication',()=>{
 for(const state of [{...run,patch_hash:'new'}, {...run,ci:{head_sha:'other'}}, {...run,validation:null}])assert.equal(publicationReadiness(state,'owner/fork').canPublish,false);
 assert.equal(publicationReadiness(run,'owner/another').canPublish,false);
});
test('passing exact-patch checks retain ordinary draft publication',()=>{
 assert.deepEqual(publicationReadiness({...run,validation:{...run.validation,status:'passed'}},'owner/fork'),{checksPassed:true,unverified:false,canPublish:true});
});
