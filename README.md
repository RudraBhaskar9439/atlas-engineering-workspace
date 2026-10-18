# Atlas — Company Brain

Atlas connects engineering evidence, people, and decisions in FalkorDB. Investigate a public GitHub issue, propose and review a patch, run checks, and open a draft pull request from one workspace. Source revisions and investigation checkpoints persist across AI providers and application restarts.

Primary hackathon entry: **Company Brain**. Secondary entry: **Agent Memory and Coordination**.

## Run locally

```bash
git clone https://github.com/RudraBhaskar9439/atlas-engineering-workspace.git
cd atlas-engineering-workspace
```

Requirements: Docker running, Node.js 20+, Python 3.12 or 3.13, and [uv](https://docs.astral.sh/uv/).

```bash
./scripts/setup.sh
./scripts/start.sh
```

Open **http://localhost:8001**. Choose **Explore demo workspace** for the synthetic Orbit environment. The read-only demo demonstrates restricted access. Those identities are for the local synthetic demonstration, not production authentication.

Setup starts a dedicated FalkorDB container on `127.0.0.1:6379`. FalkorDB Browser is available on `http://localhost:3001`. Database and application state persist in a Docker volume and `.atlas-data/`, respectively. Other projects using port 8000 are unaffected. The start script builds the application and Python test images and starts Atlas with its internal test worker.

Atlas runs in Docker by default. Run only one application instance on port 8001. The worker is internal, with no published host port; its token is stored in a dedicated Docker volume. The worker alone mounts the Docker socket to start fixed, isolated test jobs.

The frontend is React/TypeScript/Vite. FastAPI serves its production build. For frontend development, run `npm --prefix frontend run dev`; Vite proxies API calls to port 8001.

## Credentials and login

Copy `.env.example` to `.env`, then fill credentials locally. Never commit `.env`, downloaded OAuth credentials, or `.atlas-data/`.

```dotenv
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
GEMINI_API_KEY=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

Provider keys are independent. API billing applies. Google login authenticates Atlas users; it does not transfer consumer AI subscription allowances. Per-user replacement keys can be saved through **Settings** and are encrypted with a persistent local Fernet key. The API never returns credential values. Owner sessions may use the server-configured provider keys; viewer sessions cannot.

Connect GitHub in **Settings → GitHub** using a personal access token. For repositories you own, a fine-grained token can restrict access to selected repositories with Contents and Pull requests write permissions. Upstream public contributions where you are not a member currently require a classic token with `public_repo` scope; choose a short expiration. Actions access may also be needed for workflow discovery and dispatch. GitHub's repository, organization, fork, and Actions policies still apply. No GitHub client secret is required. See the [official token limitations](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#fine-grained-personal-access-tokens-limitations).

Google OAuth configuration:

1. Create a Web application client in Google Auth Platform.
2. Authorize `http://localhost:8001/auth/google/callback` exactly.
3. Configure the branding/audience and test users as required by your Google project.
4. Start Google sign-in from Atlas. The backend validates the OIDC response and verified email using Authlib.

Google sessions receive an isolated synthetic company workspace. `ATLAS_ALLOWED_EMAILS` can restrict allowed Google identities; it is checked again on authenticated requests.

For deployment beyond localhost, configure HTTPS, `ATLAS_BASE_URL`, an explicit Google account allowlist, and `ATLAS_DEMO_ENABLED=false`. The provided setup deliberately binds services to loopback. It is not a production deployment recipe.

## The demonstration

The fictional company is preparing its Orbit launch. Zara owns the launch decision. Noah authored an authentication fix reviewed by Maya. Launch approval depends on AUTH-42 being closed and authentication verification passing. Leo's annual billing decision has separate evidence.

1. Ask why the Orbit launch was approved. Inspect cited evidence and ownership paths.
2. Open **Knowledge → Decisions** and introduce the authentication regression.
3. Inspect the failed condition, source revision, affected decision, owner, and historical checks.
4. Apply the unrelated team-notes update. The launch decision is not rechecked because it does not depend on that source.
5. Resume the saved task with another provider and the planner role. Atlas revalidates cited evidence before giving the checkpoint to the receiving model.
6. Approve a proposed local investigation. Repeated approval returns its existing outcome.
7. Restart Atlas. Task progress, source revisions, and decision histories remain available.
8. Sign in as viewer. Restricted audit evidence and owner task memory are inaccessible.

All fixture people, records, URLs, and commits are synthetic. The application makes no claim to detect private employee activity, prove business decisions correct, or reproduce hidden model reasoning.

## Component ownership

| Function | Component |
| --- | --- |
| Graph storage, full-text retrieval, dependency queries, exact task records | FalkorDB and its official Python client |
| Repository structure and source analysis | FalkorDB Code-Graph 0.4.2 at pinned upstream commit, isolated Python runtime |
| Document extraction, embeddings, graph retrieval, incremental indexing | FalkorDB GraphRAG SDK 1.4.0 |
| Interactive graph | FalkorDB Canvas 0.3.3 |
| Model routing | LiteLLM, as used by Code-Graph and supported by GraphRAG SDK |
| Temporal conversational facts | Graphiti 0.31.1 with native FalkorDB storage |
| Google OIDC and signed sessions | Authlib and Starlette |
| Protected credentials and budget reservations | Fernet encryption and SQLite |

Graphiti is an external framework recommended in FalkorDB's memory documentation. It is not a sponsor-owned product. It receives task episodes, not a duplicate copy of the company-document ingestion pipeline. Exact checkpoints do not depend on episode extraction succeeding.

Code-Graph requires an older GraphRAG SDK. Its dedicated `.tools/codegraph` environment avoids that conflict with Atlas's newer document SDK. Atlas pins Code-Graph commit `f5e72c074bf8b529d71472162e6d43cd6caaa5b6` and enables its static Python resolver. `codegraph-requirements.txt` pins its parser dependencies for both local and Docker runs; the image checks the parser API during its build. Source snapshots exclude dependency manifests and virtual environments. Atlas uses Code-Graph as a dependency for analysis; the Atlas interface and decision/workflow implementation are original code. There is no second Code-Graph web app to configure.

The fixed extraction and embedding models are separate from the chat picker. Switching chat providers does not rebuild vectors. Document extraction and Graphiti episode extraction use the Gemini connection with fixed Gemini Flash extraction and Gemini embedding configuration.

## Importing records

Sources accepts public GitHub HTTPS repository URLs, UTF-8 Markdown/text documents up to 100 KB, and structured JSON records. This build structurally indexes Python repositories. Git commit metadata is read separately from the original checkout. Repository analysis uses isolated source snapshots; Atlas does not run the repository's application or install its dependencies. Symlinks and repositories above the analysis size limit are rejected.

### From a public issue to a draft PR

Open **Issues**. Enter a public GitHub repository URL or select an imported repository, choose open/closed/all issues, and click **Browse issues**. Select an issue to read its description and up to its first ten comments. Pull requests are excluded from the issue list.

An owner can click **Save issue evidence**, then **View evidence** or **Investigate issue**. Saving links a Ticket to its Repository and a versioned Source in FalkorDB. Re-saving identical evidence preserves its revision; changed evidence creates a new revision. Existing Code-Graph metadata is preserved when a repository is already indexed. The investigation explicitly references the saved source and can continue across the existing model handoff workflow.

Browsing and saving require no model calls. A connected GitHub token is used for public reads; without one, GitHub's unauthenticated rate limit applies. Description/comment limits are displayed, and evidence refreshes only when saved again.

Choose **Open change workspace**, then follow **Context → Review changes → Run checks → Publish**:

1. Atlas pins the repository's current default-branch commit. Leave **Automatic file selection** enabled and click **Generate proposed fix**. Your chosen model first selects relevant source and test files from a bounded repository file list, then reads them and proposes exact edits. No manual file selection is required. The proposal shows the files used and the selection reason. **Choose files manually** remains available when you want to adjust context.
2. Inspect the selected context and diff, review uncertainty notes, and edit proposed file contents if needed. Download the patch at any point. Editing invalidates prior checks.
3. For Python, run pytest or unittest in isolated containers. The baseline includes proposed tests but retains the original implementation; the proposed run includes the fix. Jobs have no network, credentials, or Docker socket, run as a non-root user, and have read-only source/root, resource limits, and a timeout. Dependencies already in the sandbox image are available; it does not install repository dependencies.
4. For native engines, JavaScript, or custom builds, choose **GitHub CI**. Select your source repository or a public fork, approve pushing the reviewed branch, and optionally dispatch an existing compatible workflow. Refresh checks; Atlas accepts successful workflow runs only for the recorded branch and commit. Fork Actions may require manual enablement; workflows requiring non-default dispatch inputs are outside this build.
5. After passing checks, approve draft PR publication. Atlas rechecks the source commit, issue evidence, reviewed file contents, and validation before creating a branch/draft PR. It never pushes to the default branch or merges a PR. Retry after a network failure to recover an existing PR.

Automatic discovery uses an additional request to the selected AI provider. Opening a workspace does not trigger paid discovery; clicking generation does. Source context stays limited to 1–6 UTF-8 files, up to 160 KB each and 200 KB total. Only known paths from the pinned file catalog are accepted. Duplicate, unavailable, and excess suggestions are dropped; an empty result stops generation. Successfully discovered context is saved privately and reused when retrying a failed patch request. Automatic selection can miss relevant code, so review the listed files and the proposed fix.

**Changes → Try a sample change** exercises review and real Python checks without GitHub credentials or AI spending. The sample is clearly labeled and cannot publish externally. Code-Graph structure is Python-only; generic source review and GitHub CI can work with other languages.

`demo/company-import.json` is an example of importing a person, ticket, decision, condition, and relationships without model calls. Upload it through **Knowledge → Sources**. Change the imported source metadata to make its decision need review.

In the evidence inspector, **Update this source** preserves a new immutable revision and rechecks dependent decisions. GraphRAG document imports preserve raw revisions before updating the derived index. If indexing fails, inspect the job status and retry; source preservation does not imply SDK indexing completed.

## Graph model and queries

See [ARCHITECTURE.md](ARCHITECTURE.md). Native graph work is essential: removing FalkorDB breaks source/ownership queries, dependency checks, checkpoint storage, and temporal memory.

Company entities use the `Entity` label plus their specific type. Facts cite Sources. Decisions depend on Facts and can depend on other Decisions. Sources retain immutable SourceRevision records. DecisionReview records preserve the exact conditions checked at each review. Private task graphs hold TaskRun and ChangeRun records with structured JSON state and expected revisions.

Company, restricted, document-SDK, Code-Graph, private task, and private Graphiti schemas are separated. Atlas resolves stable references through authorized backend tools; it does not assume cross-graph Cypher joins.

## Tests and API budget

```bash
uv run pytest -q
uv run ruff check atlas tests scripts
npm --prefix frontend run build
npm --prefix frontend test
uv run python -m scripts.sandbox_smoke
```

Tests exercise the real local FalkorDB and isolated graph namespaces. Model responses are stubbed only in deterministic API tests. They do not spend API credits. The paid smoke check is explicitly separate:

```bash
uv run python -m scripts.live_smoke
```

That check sends synthetic company context to Claude and Gemini, exercises evidence revalidation and a real handoff, and writes results to `.atlas-data/live-evaluation.json`. It is not a comparison benchmark or proof of production readiness.

The development budget defaults to **INR500 total**. The ledger reserves a conservative estimate before model requests and fixed allowances before SDK operations. Failed/timed-out requests retain their reservation because providers may still bill them. The estimate uses a safety margin and is not an invoice or guaranteed provider billing cap. Use provider-side spend controls too. Settings shows the current reservation balance.

## Limits

- One backend process coordinates task commits. Distributed-worker concurrency is outside this build.
- Dependency traversal is bounded at eight hops. Recorded conditions are explicit field/equality checks, not an automated judgment of arbitrary business logic.
- Initial assignments/PR explanations come from structured records. Repository analysis alone does not establish business ownership.
- Model outputs can differ across providers. Structured task state and source references are what remain consistent.
- Source revisions and decision history persist. Repository imports refresh the cached checkout and publish a Code-Graph snapshot for its commit after analysis succeeds. Document indexes remain mutable.
- Suggested investigation actions create local outcomes. The separate Changes workflow can push reviewed branches and create draft GitHub PRs after explicit approval and successful checks. Atlas does not merge PRs, create external Jira tickets, send messages, or cancel a launch.
- Passing checks validate only the selected tests/workflows. Model patches and tests require review; neither a passing run nor a failing baseline proves the fix is complete.
- The local Python sandbox has fixed dependencies and cannot validate native builds. GitHub CI requires a suitable existing workflow and permission to run it.
- Google consent completion and third-party account access require the user's own account interaction.

## Attribution and AI assistance

See [THIRD_PARTY.md](THIRD_PARTY.md). AI assistance was used for architecture research, implementation, documentation, and test design. Participants must review and understand the submitted code, queries, dependencies, and results. External components and the original Atlas contribution must be disclosed in submission materials.

Atlas's contribution is the implementation connecting versioned evidence, executable decision conditions, historical reviews, structured checkpoints, and validated provider/agent handoffs. Company brains and persistent AI memory have existing precedents; Atlas is not presented as the first system in that category.

Document extraction and temporal memory use the Gemini connection with `gemini-2.5-flash` and `gemini-embedding-001` (3072 dimensions), independently of the chat model. Chat includes bounded native Code-Graph structure from up to two authorized Python repositories; it does not claim exhaustive code analysis. Ownership relationships come from imported records and Git metadata; editing source text does not infer new ownership edges.

## Verified build — October 18, 2026

The local build passes 109 backend tests against FalkorDB, two citation tests, backend linting, frontend compilation, locked dependency validation, and Docker image builds. Real isolated test containers verify baseline/fix behavior, non-root execution, credential isolation, disabled network, and read-only files. Application and database restart recovery were exercised. Live checks used Claude and Gemini, Code-Graph, GraphRAG SDK, and Graphiti; see [the actual evaluation results](outputs/atlas-evaluation.json).

Claude and Gemini are the active verified providers. The OpenAI credential supplied during development was rejected with `401 invalid_api_key`; the user chose to defer GPT and continue with Claude and Gemini. After the user updated the OAuth configuration on October 18, 2026, a fresh sign-in request reached Google's account chooser. Google accepts `http://localhost:8001/auth/google/callback`; the earlier `redirect_uri_mismatch` is resolved. The user subsequently confirmed completing sign-in and returning to Atlas. This is recorded as user-verified; the agent's separate browser session still shows the synthetic demo account and does not independently verify the Google identity. The user exercised fork creation and a review-branch push through Atlas. Read-only verification found no matching native CI run or upstream PR. Public deployment, contest submission, and demo recording remain outstanding.

A model quality check found both Gemini and Claude describing a failed decision condition as a blocked launch. Atlas's authoritative state stays `needs_review`; the decision owner must review the evidence. A live Gemini patch check also caught missing tests, then an incorrect arithmetic expectation after retry. These failures remained blocked for publication and are retained in the evaluation. Manual review corrected the test and added the exact half-cent regression: the baseline then failed one test and the proposed fix passed all four. Treat model recommendations and patches as proposals requiring review; no production-scale or baseline retrieval benchmark is claimed.

Structured patch generation was also checked on FalkorDB issue #3246 using 115 KB of downloaded source verified against the pinned Git blobs. Claude exceeded the former output limit; Gemini first failed exact source matching, then produced a valid proposal after tighter instructions. Source review found a conflicting regression test in that proposal, which remains unvalidated with a warning. This verifies response handling and review recovery, not the correctness of the Rust fix. See [the retained attempts](outputs/atlas-structured-patch-evaluation.json).

## Phase verification

Run `python3 scripts/verify.py` to verify all six phases without paid model calls. Use `--phase foundation`, `data`, `decisions`, `memory`, `interface`, or `release` to run one phase. Results are written after each phase to `outputs/atlas-phase-report.json`; diagnostic logs stay in `.atlas-data/verification/`.

The data phase includes an actual Code-Graph re-index after deleting a Python file. Memory checks cover historical citation versions, unresolved questions, stale writes, repository commit changes, duplicate actions, and an old episode finishing after newer task progress. Google callback tests use mocked identity responses; they do not replace the user's real sign-in test.

The interface clears account-specific caches during sign-out and ignores responses from the previous account. Historical citation links open the cited revision. The task sidebar shows authoritative recorded decision status separately from model recommendations.

See [the patch evaluation](outputs/atlas-change-evaluation.json) and [isolated-runner checks](outputs/atlas-sandbox-evaluation.json) for the new change workflow. See [DEMO.md](DEMO.md) for a presentation walkthrough and [SUBMISSION.md](SUBMISSION.md) for the local submission checklist. Source packaging excludes credentials, local data, installed dependencies, and caches.

Graphiti receives the same private user namespace as both its driver database and episode/search group ID. This is required because its FalkorDB driver selects graphs by group ID. The original two synthetic demo episodes were ownership-checked and copied into the private graph; the legacy demo graph is retained for recovery and is outside current API retrieval.
