# Draft: score verified task completion

An agent that reports an unresolved CAPTCHA has explained the failure, but has
not completed the task. A revised task should not earn a high final score merely
because its rubric rewards accurate reporting, formatting or attempted access.

This draft adds opt-in scoring support. It does **not** activate a new policy on
the existing 200 tasks, edit their encrypted rubrics, regrade historical runs, or
change the evaluation platform. Dataset migration and semantic judge validation
are required before calling this a v2.1 dataset release.

## Where the rules belong

| Location | Responsibility |
| --- | --- |
| Evaluation judge system prompt | Credit completed, evidenced outcomes. A blocked attempt is not completion; successful recovery is eligible for full credit. |
| Task instructions and rubric | Specify the required result, fixed number of units, accepted sources, and any valid negative result. Tell the agent these requirements before execution. |
| Scoring code | Cap the weighted rubric score at verified completion; preserve the original rubric score for inspection. |

The new prompt lives in `completion_policy.py` and is appended to the findings
judge's system message only when the task includes a valid completion contract.
It does not go into the browser agent's system prompt.

## Score calculation

For each **essential** outcome group:

    completion = correctly completed units / required units

For the task:

    completion_cap = min(completion across essential groups)
    final_score = min(existing score after penalties, completion_cap)

A single goal, such as a confirmed registration, has one required unit. A
collection of 25 verified products has 25 units. Twenty complete products caps
the score at 80%, even if the old rubric would have awarded 100%. The cap cannot
increase a lower rubric score. Existing reward-hacking and canary penalties remain.

Use separate groups only for essential outcomes. For example, a booking and its
confirmation can both be essential. Fonts, formatting and optional extras belong
in ordinary weighted items; making those essential would create another harsh
whole-task penalty. With multiple groups, the least-complete one limits the
score. The author must review that choice rather than accept it automatically.

Counts stay fixed after a block. This draft supports finite counts; open-ended
tasks such as "find all products" need an agreed finite scope or a separately
reviewed denominator policy before opting in. Do not use the number of accessible
pages or attempted pages as the denominator.

## Synthetic task example

This example contains no benchmark answers, hidden rubrics or canaries.

The task instruction and rubric both include this exact requirement:

> Deliver 25 distinct product records, each with a price and stock status verified
> on its official product page. Blocked attempts do not count.

The task metadata includes:

```json
{
  "completion_contract": {
    "version": "verified-outcomes-v1",
    "groups": [
      {
        "id": "products",
        "required": 25,
        "requirement": "Deliver 25 distinct product records, each with a price and stock status verified on its official product page. Blocked attempts do not count."
      }
    ]
  }
}
```

The judge still reports all weighted rubric findings and additionally returns:

```json
{
  "completion_findings": [
    {
      "group": "products",
      "evidence": "Records 1–20 have corroborated prices and stock status; records 21–25 only report access blocks.",
      "completed": 20
    }
  ]
}
```

The code validates group coverage and integer counts, then calculates the cap.
The model never chooses the denominator or emits the final score. The retained
result includes the contract hash, policy version, cap, rubric score, findings,
and final score. The full task contract is retained with the existing task
artifact; the judge configuration also hashes the policy source.

## Outcomes to distinguish

| Evidence | Completion finding |
| --- | --- |
| Unresolved block; no required result delivered | Zero completed units. Accurate reporting can still satisfy an honesty item. |
| Some required records verified; others blocked | Count only complete, distinct records. |
| Challenge encountered, then required result delivered | Normal credit; no separate CAPTCHA penalty. |
| Source inspected and absence verified | Credit only if the contract explicitly accepts that negative result. |
| Site could not be inspected | Not evidence of absence. |
| Alternative source delivers the required facts | Credit only if the contract permits that source. |
| Collector omitted necessary evidence that the agent produced | Count is null, with a reason; official score remains unavailable. |
| Agent never produced the requested work | Zero, not null. |

The judge still has to interpret evidence correctly. Deterministic arithmetic
does not prove that its completed-unit count is correct. Inspect the cited
records in semantic validation.

## Dataset migration before release

1. Review all 200 task instructions and rubrics together. Define completion units
   and accepted outcomes, remove contradictory blocked-as-success branches, and
   check that every essential requirement is visible to the agent. String
   validation catches absent requirements, not semantic contradictions.
2. Review `bu2-001` and `bu2-044` as contrasting access cases. In `bu2-001`, merely
   changing the verification item's ruling removes only 15 points; it does not
   implement outcome scoring. Do not pick a denominator from one historical
   rollout. Review `bu2-171` and `bu2-185` carefully: the current contracts
   deliberately accept some unavailable/blocked outcomes.
3. Version the new task/rubric contracts together, preserve encrypted snapshots,
   and update per-task hashes, revision metadata and the 55-task subset hash.
   Preserve the distinction between verified absence and blocked observation.
4. Validate the judge on saved complete evidence and synthetic cases: blocked,
   partially completed, recovered, verified absence, permitted substitute,
   duplicate records and missing collector evidence. Compare item findings,
   completion counts and final scores; manually adjudicate disagreements.
5. Label rejudgments as the new scoring policy. Changed task instructions require
   fresh executions for comparable benchmark results. Never overwrite the
   historical scores or describe synthetic unit tests as measured judge accuracy.
6. Integrate the same contract, prompt and scorer into the eval platform before
   claiming parity. This repository alone does not update that platform.

Existing tasks without a contract retain the original prompt, response schema and
scoring behavior. This is intentional migration protection, not a claim that the
current dataset's blocked-outcome loopholes have already been fixed.

## Offline validation

```bash
python -m unittest discover -s tests -v
```

Tests exercise the real adapter with synthetic structured responses; they do
not call a model or a website. The existing 200 tasks continue to validate.
