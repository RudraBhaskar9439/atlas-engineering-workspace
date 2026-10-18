# Atlas demonstration

Open http://localhost:8001 and choose **Explore demo workspace**. The fictional Orbit company supplies clearly labeled sample evidence. Paid investigations use Claude or Gemini; Settings shows the shared spending reservation balance.

## Fast recording route

Refresh Atlas, open **Changes**, then click **Start guided example**. Each walkthrough starts with a fresh, clearly labeled sample. Previous work remains in **Workspace history**.

1. Show the prepared billing fix in **Review changes**. Explain that this is a guided example, not live AI generation.
2. Choose **Continue to checks**, then **Run checks**. The baseline and proposal run in real offline containers.
3. Show **Checks passed** and expand **View test logs**: the baseline fails and the proposal passes.
4. Choose **Review publication**, then **Download reviewed patch**. The sample cannot open an external PR.
5. Show the Knowledge graph and an existing investigation checkpoint. Demonstrate model continuity with Claude/Gemini separately from the local sample.

Suggested narration: “Atlas connects issues, source evidence, and AI context. Here is a guided change with real test execution: the baseline reproduces the bug, and the reviewed proposal passes. The same workflow supports public repositories through GitHub Actions.”

For a real native repository, choose **Run checks in my fork** after approving the proposal. This creates a draft validation PR inside a public fork owned by the connected account, so PR-triggered workflows can start. Upstream publication is a separate action after matching checks pass. This new PR-creation path is covered by deterministic tests; the agent has not opened a live validation PR.

## Suggested recording sequence

1. **Overview.** Explain the core problem: engineering context is split across issues, code, decisions, and AI sessions. Atlas connects that context and carries reviewed work forward.
2. **Issues.** Browse FalkorDB/FalkorDB, select a real issue, read its discussion, and save its evidence. Open the source inspector to show the issue's revision and provenance. Browsing and saving use no AI credits.
3. **Investigations.** Ask Claude about a saved issue or the Orbit authentication change. Open a source chip to show the exact cited revision. Switch the same investigation to Gemini; the checkpoint and evidence references continue across the handoff.
4. **Changes.** Open a change workspace from an issue. Explain the pinned commit, keep Automatic file selection enabled, and generate a proposal without choosing files. Show the actual files Atlas selected, then review the diff and notes. Show the four steps: Context, Review changes, Run checks, Publish.
5. **Real checks, safe sample.** Use **Try a sample change** if the live repository needs a native build or credentials. The annual-billing example runs real isolated Python tests: baseline fails the half-cent regression and the corrected proposal passes. Clearly identify this as synthetic; it cannot publish externally. The retained live Gemini sample also demonstrates that a proposed test can be wrong and need manual correction.
6. **Native repository checks.** For FalkorDB's native engine, select GitHub CI in a real change workspace. Explain the approved branch, existing workflow, and exact commit binding. Do not claim Python Code-Graph understands the C engine or that mocked CI results are a live build.
7. **Publish.** Show the review/approval page. For a prepared, appropriate real change with working GitHub access and successful checks, the explicit final action creates a draft PR. Otherwise explain the implemented flow and display its verification status honestly. Do not create a placeholder upstream PR for the recording.
8. **Knowledge.** Show graph search, layouts, labels, fit view, fullscreen, record inspection, and the Sources/Decisions/People tabs. In Decisions, introduce the synthetic auth regression: the dependent launch decision needs review while unrelated billing evidence remains separate.
9. **Memory and access.** Reopen a saved investigation; show current versus historical source evidence. Exact checkpoints persist independently of Graphiti extraction. The read-only demo cannot access owner conversations or restricted audit evidence. Restore the owner demo after the access demonstration.

## Claims to keep precise

- `needs_review` requests a human decision; it is not automatic launch cancellation. Sampled model answers previously overstated it; the evaluation retains this limitation.
- Google login identifies Atlas users. AI model usage uses separate provider API keys and billing, not consumer subscription tokens.
- Code-Graph structural indexing is Python-only. Source-file review and configured GitHub CI support other languages with their own build requirements.
- Local sandbox execution has no network or credentials. GitHub CI uses the destination repository's Actions environment and policies.
- A passing selected test suite is evidence, not proof of complete correctness. Review the patch and generated tests.
- If a proposal fails, keep the same workspace and retry generation or switch models. Successful automatic file selection is saved; creating another workspace is unnecessary. Structured output validation rejects incomplete or malformed patches.
- A suggested CI destination is a repository name, not an automatically created fork. Choose **Create my fork** on the checks screen if it does not exist. The default check mode uses a validation PR in that fork; manual workflows are under Check configuration. Missing repositories and unavailable Actions workflows have separate recovery messages.
- A pushed branch alone does not guarantee checks start. FalkorDB's Rust Push workflow only accepts main/version branches. The validation-PR mode addresses its Rust PR trigger. No passing native CI is claimed until an actual matching run completes.
- **Review publication** opens a preview before checks pass. The preview explains any missing validation and links back to checks. Actual draft PR creation still requires successful checks for the current patch, a GitHub connection, a destination, and explicit approval. An unvalidated sample does not claim its checks are complete.
- Runner selection follows the changed files. Native code and build configuration keep GitHub CI selected and disable the Python-only option with a visible explanation. Python proposals retain the isolated runner. The UI-triggered annual-billing sample was re-run successfully: baseline failed, proposal passed, and validation matched the current patch.
- Draft PR publishing is implemented and covered by deterministic API tests. No arbitrary live PR, production deployment, video, or contest submission has been performed.
- Google sign-in completion was confirmed by the user. GPT live inference remains deferred; demonstrate Claude and Gemini.

Screenshots and current results are under `outputs/`. Review the evaluation before quoting any test count or live external action in a recording.
