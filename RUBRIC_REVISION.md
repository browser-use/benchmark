# BU Bench V2 feedback review

**Candidate:** `2026-09-24-feedback-review-draft-4`
**Status:** draft; historical results are unchanged.

This candidate keeps the five earlier corrections from PR #32, restores the
original AliExpress and Reuters rules, and adds targeted fixes for job-search
coverage, source-count drift and blocked property lookups. It preserves all 200
task IDs, the 55-task subset membership, every item ID and every weight.

## Feedback and decisions

| Task | What the evidence supports | Candidate decision |
|---|---|---|
| 014 | Some runs deliver substantial verified cohorts. That does not make “plausibly exhausted the public population” measurable. | Keep the visible-salary correction. Define smaller-cohort completion through recorded multi-source searches, result boundaries, regional coverage and candidate decisions. Clarify the task too. |
| 028 | A full-score saved result can include Dice-only workbook rows that the old expansion item expressly ignores. Other runs admit that expansion remains partial. | Apply expansion eligibility to every retained workbook role; preserve the raw Dice anchor separately. Require actual expansion and account for discovered candidates. Clarify the smaller-cohort stopping rule in the task. |
| 029 | Officially supported address aliases can refer to one building. A shared tax lot alone is insufficient. Some reviewed deductions concern contact handling instead, so alias wording cannot justify a blanket score increase. | Retain PR #32's source-backed alias correction. Do not change historical scores or contact rules. |
| 040 | Saved evidence shows substantial successful booking research; the partner reports a full-score run. Runtime and strategy differences alone do not establish a rubric defect. | Keep the rubric. Pin equal budgets when comparing agents; the partner's particular successful trace still needs verification. |
| 048 | Supporting conflict files and the declared final table serve different purposes. Missing portions of a judge packet cannot prove missing annotations. | Retain PR #32's final-table/ledger distinction. Do not weaken eligibility or completeness requirements to compensate for incomplete judge evidence. |
| 088 | The task's numeric range conflicted with its named committee section. Successful handling of the contradiction does not make it a fair instruction. | Retain the source-backed section wording and premise correction from PR #32. |
| 113 | “Available” was ambiguous between listed and in stock. An incomplete delivered file is a separate failure. | Retain explicit inclusion of storefront-listed sold-out variants. Do not make a judge accept a bare assertion of complete extraction. |
| 163 | Retained source CSVs contain 89 with-comment rows, 64 without-comment rows and 173 final-action rows, with 172 distinct permit/date/type keys in the last report. Historical rubric counts were 88/64/170. | Use the run's captured rows and dates; historical counts are examples, not targets. Consolidate repeated appearances of the same event within and across sources. Keep source-stream and row-fidelity requirements. |
| 171 | Hash-verified successful executions obtained the requested seller feedback and delivery data. It is not inherently capped at 66%. | Restore original A6/A7 acquisition requirements, totaling 34 points. Honest unavailable markers do not complete those items. |
| 185 | Reviewed full-score executions demonstrate inspected absence, not footer-video playback. A blocked page does not establish absence. | Remove PR #32's expanded blocked-result credit; retain the original evidenced-absence branch. A playback-only task needs a verified target and revised instructions. |

Earlier authorized follow-ups remain: **053** requires an accessible external
property lookup; a block alone does not complete reconciliation. **187** treats
zero delivered listings as failure to meet the required cohort size, while keeping
unavailable judge evidence distinct from unfinished work.

## Evidence reviewed

The September 24 review queried the new platform's Laminar project directly over
the August 1–September 24 window. It found 101 scored datapoints across these
12 task IDs in the 200-task category. These include different harnesses, repairs
and judge replays; they are not 101 independent, comparable executions.

The review also examined the 72 saved Astra/Fable results for these tasks, selected
original GitHub runner artifacts with matching archive and result hashes, current
task/rubric text, the partner's feedback and retained official-source CSVs.
Private captures, complete rubrics, personal source fields and raw traces are not
included in this document.

A high historical score is evidence to inspect, not proof that the requested work
was completed. No reviewed Reuters execution demonstrates in-scope playback.
The exact permit rows disputed by the partner have not been supplied; the retained
CSVs support a count/deduplication correction, not a claim to have identified the
partner's precise missing row.

## Validation and remaining work

- The existing artifact validator checks the nine declared revised tasks, hashes,
  all 200 IDs, unchanged weights/canaries and unchanged 55-task membership.
- Eleven pre-existing semantic cases remain; no tests or cases were added.
  Their revision label was refreshed; they do not validate the newly added rules.
- New semantics for 014/028/053/163/187 still need saved-evidence judge validation.
  This review inspected historical judgments and evidence without making paid calls.
- An earlier synthetic 113 “positive” case produced `not_assessable` because it
  asserted completeness without supplying result rows. This is not a successful
  semantic validation and is not a reason to weaken the evidence requirement.
- Combined criteria in 014 remain. Splitting them and reallocating their weights
  requires a separate scoring revision.

Task instructions change for 014/028 as well as the earlier 029/088/113 edits.
Historical runs must not be presented as executions of the revised instructions.
Any future saved-trace rejudging must record both rubric versions.

## Reproduce artifact checks

```bash
uv run python review_rubric_revision.py
uv run python review_rubric_revision.py --write-private-diffs
```

The second command writes plaintext task/rubric diffs into ignored
`run_data/rubric-review/`. Keep those private. The original encrypted August 25
dataset remains in `snapshots/BU_Bench_V2_2026-08-25.enc`. This candidate adds no
score-cap machinery and makes no changes to historical results.
