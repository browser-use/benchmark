# BU Bench V2 rubric revision candidate

**Revision:** `2026-09-23-rubric-contract-draft-1`
**Status:** draft contract corrections; original partner runs have not been regraded.

The previous release contains task/rubric conflicts. A change of judge model alone
cannot resolve them. This candidate corrects seven task contracts without changing
the 200-task population, item IDs, weights, or global scoring/penalty policy.

| Task | Candidate correction | Remaining validation |
|---|---|---|
| `bu2-014` | Null no longer earns field credit when consulted evidence discloses a value. | Long combined criteria and the smaller-population search rule remain unresolved. |
| `bu2-029` | Officially evidenced address aliases can identify one building; shared tax lot alone cannot. | The reported partner run and its official mapping still need review. |
| `bu2-048` | Evaluate the declared final table with supporting ledger/conflict files; clipped supporting content is not proof of a missing annotation. | The harness must actually supply full files; this dataset edit cannot repair an artifact mount or truncated prompt. |
| `bu2-088` | Remove the conflicting numeric range from the task; request the named committee section and its source-backed range. | Revised instructions require a new attempt for a comparable benchmark result. |
| `bu2-113` | State explicitly that all storefront-listed variants, including sold-out variants, are in scope. | Prospective clarification: do not retroactively punish a reasonable reading of the old task. |
| `bu2-171` | Honor evidenced unavailable fields in the two criteria totaling 34 points. | Check positive and negative access cases; this does not prove every reported 66% run was correct. |
| `bu2-185` | Separate a genuine blocked/failed-load outcome from an inspectable page with no player. | Compare against complete traces showing genuine blocks, unsupported block claims, and accessible pages. |

The `bu2-029`, `bu2-088`, and `bu2-113` instructions change. Their old traces can
help review the proposed semantics but are not fresh executions of the new tasks.
The other four edits concern rubric interpretation. Rejudging saved evidence must
still record both rubric versions; historical published results are unchanged.

## Deferred issues

- `bu2-028`: expanded-cohort completeness has no sufficiently explicit finite
  target or stopping rule. Agree that rule in the task before assigning it weight.
- `bu2-163`: the reported duplicate/omitted record has not been independently
  reconciled. Obtain exact rows, identities, dates, source snapshot, and the judge
  input before changing a factual answer key.
- `bu2-040`: a turn cap and a wall-clock cap measure different constraints.
  A successful direct-UI strategy versus a timed-out pipeline is not by itself
  proof of a rubric defect. Pin the run budget and demonstrate feasibility.
- `bu2-014`: atomic criteria and a bounded search contract need a separate revision.

## Review without publishing benchmark text

`BU_Bench_V2.enc` is the candidate in this branch. The exact previous encrypted
artifact is preserved at `snapshots/BU_Bench_V2_2026-08-25.enc`, using the same
`BU_Bench_V2` decryption key name. `rubric_revision.json` records artifact and
per-task before/after hashes. Original per-task hash fields have mixed historical
provenance; revised task/rubric hashes cover the exact distributed UTF-8 strings.
Source verification dates retain their original meaning; this edit does not claim
a new live-site verification.

```bash
uv run python review_rubric_revision.py
uv run python review_rubric_revision.py --write-private-diffs
```

The second command writes all seven readable diffs and 15 encrypted-at-rest
synthetic review cases into ignored `run_data/rubric-review/`. These cases specify
expected semantic judgments, not measured model accuracy. Run them against the
candidate judge and have reviewers adjudicate disagreements before release.
Never commit or publish those plaintext files.

For a partner comparison, freeze the exact task/rubric revision, task IDs, judge
model/reasoning, evidence files, screenshot policy, tool set, and runtime limits.
Keep incomplete judge evidence separate from an adjudicated task failure. The
existing scorer still gives `not_assessable` no credit; this candidate does not
change that global policy or claim missing evidence has been recovered.
