# BU Bench V2 rubric revision candidate

**Revision:** `2026-09-24-access-outcomes-draft-3`
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
| `bu2-053` | A blocked external lookup does not complete property reconciliation; an accessible no-match or uncertain result remains valid. | Saved-trace review pending; no new judge calls. |
| `bu2-187` | An observed block is incomplete work, not missing judge evidence; no delivered listings fails the required cohort size. | Saved-trace review pending; no new judge calls. |

The September 24 follow-up corrects rubrics 053/187 and restores the original
August 25 task objects for 171/185, reverting their changes from PR #32:

- AliExpress 171 again requires actual seller/feedback and shipping/delivery
  values for the two acquisition items totaling 34 points.
- Reuters 185 no longer has the added blocked/failed-load branch that could
  satisfy every applicable item without observing playback.

All 200 task instructions and weights remain unchanged from the preceding
revision. Accurate failure reporting remains valid, but does not itself earn
credit for required work left undone. The short system rule makes that distinction;
other task-specific alternatives remain unchanged. No score cap or new scoring
machinery is introduced. Historical scores have not been regraded.

The `bu2-029`, `bu2-088`, and `bu2-113` instructions change. Their old traces can
help review the proposed semantics but are not fresh executions of the new tasks.
The other four edits concern rubric interpretation. Rejudging saved evidence must
still record both rubric versions; historical published results are unchanged.

## Saved-run feasibility check (September 24)

Six archived platform executions per task, three Astra and three Fable, show:

- AliExpress 171: four scored 100% under the original acquisition requirements.
  Astra repetition 2's downloaded runner archive matches GitHub's SHA-256 and
  the archived result hash. Its tool outputs contain actual seller feedback,
  shipping and delivery values for the ranked listings. Keep the restored A6/A7
  requirements; an unresolved block is not completion.
- Reuters 185: three scored 100% for an evidenced absence of an in-scope player,
  not successful playback. Five runs reached the page; one was blocked. No
  reviewed run demonstrates the requested footer-video playback. Astra repetition
  2's hash-verified archive contains footer inspection and zero-player probes.
  Preserve the original evidenced-absence branch, distinguish it from an
  inaccessible page, and do not claim this is a demonstrated playback task.
  A playback-only benchmark needs a verified target and revised task wording.

These are historical platform records from September 17, not an exhaustive
all-time search or fresh site verification. The legacy live eval connector returned
HTTP 401. No paid judging or historical score changes were made. The original
AliExpress/Reuters task objects remain exactly restored from the August 25 snapshot.

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

The second command writes seven plaintext diffs for local review and copies the
11 synthetic review cases as `review-cases.private.enc` into ignored
`run_data/rubric-review/`. The diffs contain task/rubric text and must stay local;
the 11 retained encrypted cases cover the five remaining earlier corrections.
They specify expected semantic judgments, not measured model accuracy. The two new rubric
changes are explicitly marked pending saved-trace review; no cases were added.
The four cases for the reverted 171/185 changes were removed from this candidate. Run the existing cases against the candidate judge and
review the new corrections on saved traces before release. Never commit or publish the diffs.

For a partner comparison, freeze the exact task/rubric revision, task IDs, judge
model/reasoning, evidence files, screenshot policy, tool set, and runtime limits.
Keep incomplete judge evidence separate from an adjudicated task failure. The
existing scorer still gives `not_assessable` no credit; this candidate does not
change that global policy or claim missing evidence has been recovered.
