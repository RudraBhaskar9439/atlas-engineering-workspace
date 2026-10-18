import {test} from 'node:test';
import assert from 'node:assert/strict';
import {citationLinks} from '../src/citations.js';
const nodes=[{id:'PR-128',kind:'Source',revision:3}];
test('mixed relation text retains a verified, clickable historical source',()=>{
 assert.equal(citationLinks('[person-noah:AUTHORED:change-auth, PR-128@1]',nodes),'[PR-128@1](#source=PR-128&revision=1)');
});
test('future and unknown citations are marked, existing markdown links stay intact',()=>{
 assert.equal(citationLinks('[PR-128@4]',nodes),'PR-128@4 (unverified reference)');
 assert.equal(citationLinks('[UNKNOWN@1]',nodes),'UNKNOWN@1 (unverified reference)');
 assert.equal(citationLinks('[PR-128@1](https://example.com)',nodes),'[PR-128@1](https://example.com)');
});
