# Atlas architecture

## Data flow

The React workspace calls one FastAPI backend. The backend selects authorized FalkorDB graph scopes, runs bounded graph queries, retrieves source revisions, validates checkpoint evidence, and supplies that context to the selected model. The model explains evidence; it cannot mutate graph records or approve actions.

FalkorDB is the authority for company relationships, source history, decision reviews, and exact task state. SQLite is limited to account metadata, encrypted provider keys, operation reservations, and integration jobs. The filesystem retains raw imported document revisions and persistent encryption/signing keys.

## Named graphs

- `atlas_{tenant}_public`: ordinary company entities, source records, decisions, and reviews.
- `atlas_{tenant}_restricted`: restricted project evidence and decisions.
- `atlas_tasks_{tenant}_{uid_hash}`: private TaskRun and ChangeRun records.
- `atlas_docs_{tenant}_{scope}`: GraphRAG SDK's own extraction and retrieval schema.
- Code-Graph graph names are returned by the isolated analyzer's installed version.
- `atlas_memory_{identity_hash}`: Graphiti's temporal episode schema, physically separated by user.

The server derives graph names and permitted scopes. Clients cannot submit arbitrary graph names or Cypher. A task derived from restricted context requires that access on subsequent retrieval. The app rechecks the current allowlist for Google sessions.

## Core queries

Trace launch support:

```cypher
MATCH (d:Decision {id: $decision_id})-[:DEPENDS_ON*1..8]->(f:Fact)-[:CITES]->(s:Source)
RETURN DISTINCT properties(f), properties(s)
```

Find decisions affected by a changed source:

```cypher
MATCH (d:Decision)-[:DEPENDS_ON*1..8]->(f:Fact)-[:CITES]->(s:Source {id: $source_id})
RETURN DISTINCT d.id
```

Find recorded owners:

```cypher
MATCH (p:Person)-[:OWNS]->(d:Decision)
RETURN p.title, d.title
```

Source provenance:

```cypher
MATCH (s:Source {id: $source_id})-[:HAS_REVISION]->(v:SourceRevision)
RETURN properties(v)
ORDER BY v.revision DESC
```

Reject stale task updates:

```cypher
MATCH (t:TaskRun {id: $id, owner: $uid, revision: $expected})
SET t.state=$state, t.revision=$revision, t.updated_at=$time
RETURN t.id
```

Native full-text search seeds retrieval using Entity title/content fields. The small demonstration's authorized graph is then supplied with ownership and decision paths. This intentionally bounded context is suitable for the fixture; it is not a claim of enterprise-scale retrieval.

## Evidence and decision semantics

Source updates compare an expected revision and content hash. Identical input creates no revision. Changed content is saved with an immutable SourceRevision. Decision conditions inspect explicitly recorded source fields. Failed or absent conditions produce `needs_review`; they do not prove that a decision is incorrect. Conditions and evidence versions are recorded in DecisionReview history.

Imported text is evidence, not executable policy. Conditions use vetted field comparisons; the app does not execute model-generated Cypher. Confirmed dependencies are imported explicitly. No automatic business dependency is inferred solely from similar wording.

## Checkpoint semantics

Checkpoints preserve objective, observable findings, source revisions, completed steps, unresolved questions, model/provider metadata, handoffs, and action outcomes. They do not contain hidden chain of thought. Resumption compares cited evidence against current authorized sources, then supplies changes and current evidence to the receiving model.

A model request can finish while another request advances the task. Expected-revision checks prevent the older response from overwriting newer state. Local approvals use stable action identifiers and return an already recorded outcome on duplicate requests.

Exact task state commits independently of Graphiti episode extraction. Episode processing has explicit status and integration-job records. There is no claim of one atomic transaction spanning Atlas, GraphRAG SDK, and Graphiti graphs. Interrupted jobs remain visible for inspection and retry.

## Security boundaries

- Authlib validates Google OAuth state and OIDC identity; sessions use signed HttpOnly cookies.
- Mutating authenticated endpoints require a session CSRF token; cross-origin writes are rejected.
- Owner, engineer, and viewer scopes determine company retrieval; private task graphs include owner checks.
- Backend-only provider credentials are encrypted with Fernet. The encryption key stays in a private local file.
- Imported files are bounded and source code is analyzed without running repository application code.
- The local demo grants synthetic access only. Public hosting requires disabling demo mode and configuring explicit Google account access.
- Provider exception messages are sanitized before returning them to users or job records.

## Revision and account boundaries

A checkpoint records the source revision actually cited, including historical references, plus the authorized repository graph and commit supplied to the model. Unverifiable references are flagged. Resumption checks both source revisions and repository changes. Existing unresolved questions persist across turns.

Repository imports are serialized. Public re-imports refresh only Atlas's disposable checkout; the user's original repository is untouched. Each commit receives a separate native code graph. Atlas updates the company repository link only after indexing succeeds, preserving the previous usable graph after failure.

Episode completion is conditional on the checkpoint still carrying that ingestion key. An older episode may finish successfully while a newer checkpoint remains pending. Google identity, role and graph boundaries are enforced on the backend; the interface also clears caches and discards stale account responses after sign-out.

Memory recall is a CSRF-protected POST because it can incur API charges. Incomplete decision evidence and dependency traversal limits produce review status rather than approval.

Graphiti group IDs equal the derived private memory graph name for every episode and search. A driver database alone does not provide isolation because Graphiti can clone the FalkorDB driver using the requested group ID.

## Public GitHub issue evidence

Authenticated users can browse a public repository's issues through fixed-host GitHub REST requests. A request-scoped credential is used when connected; otherwise reads are unauthenticated. Repository names are validated, redirects are rejected, and the backend verifies that the repository is public. Viewers can browse; only owners can save issue evidence through the existing CSRF boundary.

An import stores a deterministic Ticket, a versioned Source, and a repository relationship in the caller's company graph. Repository Code-Graph metadata is retained. The Source includes bounded description/comment text, GitHub provenance, issue state, assignees, labels, and explicit truncation counts. It is a snapshot, not a webhook synchronization. Re-saving retrieves fresh GitHub data and uses the normal source revision/hash rules.

Explicit source identifiers and full-text matches are prioritized within the existing twenty-source context limit. Issue text remains untrusted evidence. Investigations propose fixes; they do not execute repository code or publish GitHub changes.

## Reviewed code changes

A private ChangeRun pins the public repository tree, source commit, and saved issue evidence revision. Context selection accepts up to six UTF-8 files, at most 160,000 bytes each and 200,000 bytes combined. The same per-file and combined byte limits apply to reviewed patches and manual edits. Patch proposals allow up to 260,000 JSON context characters; ordinary investigations retain their 65,000-character limit. The full prompt is priced and reserved before inference. The model returns bounded exact text replacements; the server validates unique matches, safe paths, allowed source files, and patch size. Every saved proposal has a hash and revision. Edits invalidate checks. Per-user ownership checks and revision conditions protect reads and writes. The interface also discards responses from a previous account.

Python validation builds two bounded source archives: the original implementation plus proposed tests, and the full proposed change. An internal authenticated worker starts fixed pytest/unittest jobs in disposable Docker containers. Jobs run as UID 10001, without network, credentials, or Docker socket; root and source are read-only, temporary storage is bounded, and CPU/memory/PID/time limits apply. Only the worker mounts the Docker socket. The worker is trusted infrastructure and is not a public endpoint. The sandbox image has fixed dependencies; repository dependency installation and arbitrary command execution are not exposed.

For other languages or custom builds, Atlas can push the reviewed branch to an approved public source repository or fork and dispatch an existing GitHub Actions workflow. CI results must match the recorded head SHA, branch, and selected workflow. Selected latest runs must all succeed. GitHub CI runs under the repository's own Actions permissions and environment; it is separate from the local offline sandbox.

Publication requires explicit approval bound to the proposal hash, passing checks for that hash, unchanged issue evidence, and the original upstream commit. Atlas verifies remote changed filenames and Git blob hashes against reviewed content before a draft PR. Branch names derive from the run and patch. A persisted publication-start marker locks edits while an external outcome is uncertain; retry recovers the same branch/PR. No default-branch push or automatic merge is supported. Fine-grained token scope and upstream contribution limitations follow GitHub's current policy.

Checks prove only the selected tests or workflows passed. Model-generated tests can be wrong; baseline failure alone is not proof of a meaningful regression. The UI exposes both logs and permits manual review.

## Automatic source context

Omitting paths or passing an empty list to proposal generation enables automatic discovery. A bounded catalog is assembled from supported text/source files in the pinned tree, prioritizing issue path references and title matches. The selected model returns a ranked list of known file paths and a short explanation. Issue text, comments, and filenames remain untrusted data. No repository code executes during discovery.

The server accepts only paths present in that catalog and within per-file, combined-byte, and six-file limits. Duplicate, unavailable, and excess suggestions are removed in order; no valid context means no patch request. Actual UTF-8 content is independently checked after fetching pinned blobs. Discovery has its own budget reservation, separate from patch generation. The private ChangeRun retains selected paths, reason, provider/model, bounded-list status, and usage before patch generation, so retries can reuse successful discovery. The interface reloads the current revision after a proposal failure. Explicit manual paths retain their previous behavior.

Selection is a proposal, not an exhaustive analysis. The review page lists the actual files supplied to the patch model. A final approved publication still requires the exact patch and successful validation.

File selection and patch generation request structured JSON schemas through the provider router and validate the returned field types before use. Incomplete output is rejected, without the continuation footer used for ordinary chat. Malformed JSON or source strings are never repaired into a patch. Exact source matching, path and byte limits still apply independently. Diagnostics retain model, schema, finish reason, and token counts without storing model text or credentials. Invalid responses preserve saved context for a retry or provider switch.

### Recording and native CI flow

Routine setup and queued checks use neutral inline status rather than repeated warnings. A clearly labeled guided example creates fresh synthetic source and runs real isolated Python tests; prior examples remain in workspace history. It does not impersonate AI generation or publish externally.

Native CI can create a draft validation PR inside the connected user’s own public fork. Fork network, ownership, reviewed branch contents and current patch approval are checked before writes. A CI checkpoint is saved before dispatch/PR creation. Retries search for an existing fork PR, allowing recovery after an uncertain response without duplicate publication. This validation PR does not link the upstream issue or open an upstream PR.

A proposal remains pinned to its recorded base and tested head when upstream advances. CI and draft publication no longer require restarting solely because unrelated upstream commits arrived. Exact branch/file hashes and matching successful checks still gate upstream publication; GitHub’s merge checks remain responsible for the target branch at merge time. Atlas never merges automatically.

### Patch response limits

Patch generation requests up to 12,000 output tokens, bounded by the provider catalogue limit, and retries once with up to 16,000 only when the provider reports truncated structured output. Each attempt reserves API budget separately. Larger structured requests allow 120 seconds. Partial or malformed proposals are never saved; the prior reviewed proposal remains intact when generation fails. Chat and automatic file discovery keep their existing limits.

### Linked-issue validation policy

When a fork enables the `pr-linked-issue` workflow, Atlas creates or reuses a tracking issue in that same owned fork and links its draft validation PR with a closing keyword. Issues are enabled in the fork if needed. Tracker lookup is paginated and retries recover an issue created before a lost response. Existing PR descriptions are preserved when adding the link. Native build and test failures remain blocking; no upstream issue is closed or upstream PR opened by this step.
