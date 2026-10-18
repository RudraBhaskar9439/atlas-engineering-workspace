# Atlas: company-brain recording guide

Lead with the company brain: **who did what, who owns it, and which decision needs attention when evidence changes.** Keep the graph, source evidence, and model continuity at the center of the video.

## Ready-to-use recording setup

- Atlas: http://localhost:8001/ — use your current owner account.
- Public sample repository: https://github.com/RudraBhaskar9439/atlas-orbit-demo
- Public sample issue: https://github.com/RudraBhaskar9439/atlas-orbit-demo/issues/1
- Team context: https://github.com/RudraBhaskar9439/atlas-orbit-demo/blob/main/COMPANY_CONTEXT.md
- Importable records: https://github.com/RudraBhaskar9439/atlas-orbit-demo/blob/main/company-records.json

The recording dataset is labeled **recording sample** in Atlas, with source IDs starting `OD-`. Those new records were added without overwriting existing source records. The people, team assignments, and auth review are fictional. Real GitHub commits, issue metadata, model calls, and isolated tests remain real.

The repository's initial five tests pass on GitHub. Its deliberately uncovered half-cent billing case is a sample issue for the optional code scene. The same issue has been investigated in Atlas with automatic file selection and a real baseline/fix test run; no draft PR was published automatically.

## Opening sentence

“Atlas gives an engineering team a shared memory of who changed what, who owns the affected service, and what needs attention next—with evidence that stays available when the AI model changes.”

## Suggested 3–4 minute story

### 0:00–0:20: start with one concrete company question

Open the investigation area and ask:

> In the Orbit recording dataset, who authored the authentication change, who reviewed it, who owns authentication, and what is Noah assigned to do next? Use OD-TEAM, OD-AUTH-REVIEW, and OD-ASSIGNMENT as evidence. Cite source IDs and revisions. These are fictional sample team records, separate from real GitHub contributor metadata.

The sample records identify Noah as the author, Maya as reviewer and auth owner, and Noah's next assignment as replay-test coverage. Open a citation so viewers see the actual supporting record and revision.

### 0:20–1:15: make the company relationships visible

In **Knowledge**, show **People** and the graph. Use the entries labeled recording sample. Trace:

- Noah → authored auth change → modifies authentication.
- Maya → owns authentication and reviews the auth record.
- Noah → assigned to replay-test coverage.
- Zara → owns the launch decision.
- Leo → owns billing and the separate billing decision.

Explain that assignment is recorded evidence, not inferred live employee activity. Show one ownership path and its source; keep navigation deliberate.

### 1:15–2:15: show why the graph matters

Open the source `OD-AUTH-VERIFY`. In its source editor, change the verification text to “Synthetic authentication verification failed: replay coverage found a regression.” Set the metadata result to `failed`, preserving the other fields:

```json
{"synthetic": true, "type": "verification", "result": "failed"}
```

Save the source, then open **Decisions**. **Approve Orbit recording launch** should move from current to needs review. **Enable annual billing · recording sample** should remain current because its condition uses separate billing evidence.

Ask:

> What changed in OD-AUTH-VERIFY, which decision now needs review, who owns that decision, and why is the billing decision unaffected? Cite the current revisions. A failed condition requests owner review; it does not automatically cancel a launch.

This is the strongest scene: a change in evidence reaches the relevant decision and responsible person through explicit relationships.

### 2:15–3:00: hand off the same investigation

Keep the same investigation open. Switch from Claude to Gemini using the model selector, then ask:

> Continue this investigation with the same recorded owners, constraints, and evidence references. What changed since the previous checkpoint, which earlier conclusions need revalidation, and what should Zara review next?

Show the saved investigation and current source citations. Demonstrate durable task context and evidence revalidation; do not claim models preserve identical hidden reasoning or always give the same answer.

### 3:00–4:00: optional action from company context

Open **Changes** and choose the Orbit demo billing issue. The reviewed proposal and real isolated test result are prepared. Show:

1. The relevant source and test file selected automatically.
2. The small diff and test review.
3. Baseline failure reproducing the half-cent case, then proposed tests passing.
4. **Review publication**, destination `RudraBhaskar9439/atlas-orbit-demo`, and the explicit approval checkbox.

A final user click can create a public draft PR in the demo repository. You own this repository, so no fork is needed. Do not create a PR in upstream FalkorDB just for the recording.

If the recording limit is short, keep the company/evidence/model-handoff scenes and shorten this coding section.

## Claims to keep precise

- Team ownership, assignments, and review history are labeled fictional records; actual GitHub author metadata remains real.
- The company graph uses FalkorDB relationships, source revisions, and deterministic decision conditions. Model explanations do not authoritatively cancel a launch.
- Recorded assignments show what the team records say someone is working on; Atlas does not monitor a person's screen or infer private activity.
- Model continuity uses saved investigation state, evidence references, and memory. Provider API keys and billing are separate from consumer ChatGPT/Claude/Gemini subscriptions.
- Local Python checks execute in isolated containers with no network, credentials, or Docker socket. Passing a selected suite is evidence, not proof of complete correctness.
- A reviewed, exact-patch passing result enables ordinary draft publication. For native GitHub checks that fail or remain pending, a user can explicitly approve an **unverified draft** tied to the same recorded patch, head, and fork. The PR discloses that status; Atlas never merges it.
- The older FalkorDB fork run failed while pushing images to the upstream organization's registry. Do not describe that run as passing. Use the small Python repository for the recording.

## Preparing another take

Restore `OD-AUTH-VERIFY` to a passed result and matching verification text before another impact demonstration. Saving creates another source revision and preserves history.

For Atlas's original built-in Orbit dataset, **Knowledge → Decisions → Fail sample check** changes the original authentication verification. Those controls do not target the separate `OD-` recording records.

The guided billing sample under **Changes → Start guided example** is another offline fallback. It is clearly labeled synthetic, runs real tests, and downloads a patch without publishing a PR.
