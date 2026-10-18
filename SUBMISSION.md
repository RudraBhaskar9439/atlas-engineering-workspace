# Atlas — Company Brain

Atlas connects engineering evidence, ownership, and decisions in FalkorDB, then preserves an investigation's context when its AI provider changes. It also links public issues to reviewed source patches, isolated Python tests or existing GitHub CI, and explicitly approved draft PRs. Its central demonstration is a launch decision whose supporting evidence changes: Atlas identifies the affected dependency, records the changed source version, finds the owner, and revalidates the saved investigation before another model continues.

Primary track: **Company Brain**. Secondary track: **Agent Memory and Coordination**. Recheck the organizer's current submission form for the exact track names and entry rules.

## Included locally

- Original backend and workspace interface, synthetic company records, and Python repository fixture.
- Setup instructions, dependency locks, container configuration, and phase verification.
- Graph model and core Cypher queries in `ARCHITECTURE.md`.
- Actual verification results in `outputs/atlas-phase-report.json` and `outputs/atlas-evaluation.json`.
- Presentation walkthrough in `DEMO.md` and interface screenshots under `outputs/`.
- AI assistance disclosure and third-party attribution in `THIRD_PARTY.md`.

FalkorDB owns storage and graph queries; sponsor Code-Graph, GraphRAG SDK, and Canvas supply repository analysis, document retrieval, and visualization. Graphiti supplies the external temporal episode layer; Atlas stores exact task checkpoints and approval outcomes directly in FalkorDB.

## Before submitting

- Publish the source to the chosen GitHub repository; review the source and understand the architecture.
- Record the demonstration and provide its video URL.
- Supply a deployment URL or the complete local setup, according to the current contest rules.
- Demonstrate the verified Claude and Gemini integrations. Google redirect acceptance was checked in the browser, and the user confirmed a successful sign-in return to Atlas. GPT verification is deferred and should not be claimed.
- Disclose observed limits: synthetic evaluation scope, advisory model wording, Python-only structural indexing, selected-check coverage, and any external publishing not verified live.
- Complete the organizer's form before the published deadline. Atlas has not been submitted automatically.

No winning score, production-scale benchmark, or novelty guarantee is claimed.

## Current workflow and evidence

The interface uses Overview, Issues, Changes, Investigations, and Knowledge, with Settings separate. Knowledge contains Graph, Sources, Decisions, and People. Changes follows Context, Review changes, Run checks, and Publish. The graph supports search, layouts, readable labels, fit, fullscreen, filtering, and record inspection.

The release includes deterministic tests for private change state, exact edits, revision conflicts, credential encryption, isolated execution, publication integrity, retry recovery, and GitHub CI commit matching. Actual local test containers are also exercised. The live Gemini patch evaluation retains two failed attempts rather than claiming the model always writes valid tests. Report the final reviewed correction separately.

GitHub connection and branch/PR writes are different verification steps. Check `outputs/atlas-evaluation.json` for their current status. No public hosting, source publication, contest submission, or demo video is part of this local delivery.
