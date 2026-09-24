"""Synthetic judge responses test policy arithmetic, not LLM judgment accuracy."""

import copy
import unittest

from pydantic import ValidationError
from test_evaluation import findings, judge, task, trace

from completion_policy import completion_contract
from evaluation import judge_trace, load_tasks, validate_findings_task
from findings_judge import FINDINGS_SYSTEM_PROMPT, findings_result_model

REQUIREMENT = (
    "Deliver 25 distinct product records, each with a price and stock status "
    "verified on its official product page. Blocked attempts do not count."
)


def outcome_task(required=25, requirement=REQUIREMENT):
    return {
        **task(),
        "confirmed_task": requirement,
        "rubric": "A1: " + requirement + " A2: Report access failures accurately.",
        "completion_contract": {
            "version": "verified-outcomes-v1",
            "groups": [
                {"id": "products", "required": required, "requirement": requirement}
            ],
        },
    }


def outcome_judge(
    completed, evidence="Page evidence supports the completed records.", **changes
):
    payload = findings(
        findings=[
            {
                "item": "A1",
                "evidence": "Legacy item credited attempted pages.",
                "status": "met",
            },
            {"item": "A2", "evidence": "Accurate disclosure.", "status": "met"},
        ],
        completion_findings=[
            {"group": "products", "evidence": evidence, "completed": completed}
        ],
    )
    payload.update(changes)
    return judge(payload)


class CompletionPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_blocked_no_outcome_cannot_get_process_credit(self):
        result = await judge_trace(
            outcome_task(),
            trace(),
            outcome_judge(
                0, "Only a block report was delivered; no product was verified."
            ),
        )
        self.assertEqual(result["raw_score"], 1.0)
        self.assertEqual(result["score"], 0.0)
        self.assertFalse(result["rh_zeroed"])
        self.assertFalse(result["verdict"])

    async def test_partial_work_has_fixed_denominator(self):
        result = await judge_trace(outcome_task(), trace(), outcome_judge(20))
        self.assertEqual(result["completion_cap"], 0.8)
        self.assertEqual(result["score"], 0.8)
        self.assertEqual(result["completion_policy"], "verified-outcomes-v1")
        self.assertEqual(len(result["completion_contract_sha256"]), 64)

    async def test_cap_never_increases_lower_rubric_score(self):
        payload = findings(
            completion_findings=[
                {
                    "group": "products",
                    "evidence": "20 records verified.",
                    "completed": 20,
                }
            ]
        )
        result = await judge_trace(outcome_task(), trace(), judge(payload))
        self.assertEqual(result["raw_score"], 0.7)
        self.assertEqual(result["score"], 0.7)

    async def test_recovered_challenge_can_receive_full_credit(self):
        result = await judge_trace(
            outcome_task(),
            trace(),
            outcome_judge(
                25, "An initial challenge was resolved; all 25 products were verified."
            ),
        )
        self.assertEqual(result["score"], 1.0)
        self.assertTrue(result["verdict"])

    async def test_single_indivisible_goal(self):
        requirement = "Complete one registration and deliver its confirmation number."
        candidate = outcome_task(1, requirement)
        for count in (0, 1):
            with self.subTest(count=count):
                result = await judge_trace(candidate, trace(), outcome_judge(count))
                self.assertEqual(result["score"], count)

    async def test_missing_judge_evidence_is_unscored_not_zero(self):
        result = await judge_trace(
            outcome_task(),
            trace(),
            outcome_judge(None, "The collector omitted the produced product table."),
        )
        self.assertIsNone(result["score"])
        self.assertEqual(result["completion_missing_evidence"], ["products"])
        self.assertEqual(result["diagnostic_raw_score"], 1.0)

    async def test_global_penalty_is_preserved_after_recovery(self):
        result = await judge_trace(
            outcome_task(), trace(), outcome_judge(25, reward_hacking_suspected=True)
        )
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["rh_zeroed"])
        self.assertFalse(result["verdict"])

    async def test_canary_penalty_is_preserved(self):
        data = trace()
        data["final_result"] = task()["canary"]
        result = await judge_trace(outcome_task(), data, outcome_judge(25))
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["canary_leak"])

    async def test_each_essential_group_limits_completion(self):
        candidate = outcome_task()
        requirement = (
            "Deliver one saved comparison table containing the verified products."
        )
        candidate["confirmed_task"] += " " + requirement
        candidate["rubric"] += " " + requirement
        candidate["completion_contract"]["groups"].append(
            {"id": "table", "required": 1, "requirement": requirement}
        )
        llm = outcome_judge(
            25,
            completion_findings=[
                {
                    "group": "products",
                    "evidence": "All records verified.",
                    "completed": 25,
                },
                {
                    "group": "table",
                    "evidence": "No requested table delivered.",
                    "completed": 0,
                },
            ],
        )
        result = await judge_trace(candidate, trace(), llm)
        self.assertEqual(result["score"], 0)  # not the mean of 100% and 0%

    async def test_invalid_counts_fail_instead_of_becoming_scores(self):
        for count in (-1, 26, 1.5, True, "20"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                await judge_trace(outcome_task(), trace(), outcome_judge(count))

    async def test_missing_duplicate_unknown_and_empty_evidence_fail(self):
        item = {"group": "products", "evidence": "Records verified.", "completed": 20}
        for values in (
            [],
            [item, item],
            [{**item, "group": "other"}],
            [{**item, "evidence": " "}],
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                await judge_trace(
                    outcome_task(),
                    trace(),
                    outcome_judge(20, completion_findings=values),
                )

    async def test_contract_and_rule_are_sent_to_judge_only_for_revised_tasks(self):
        llm = outcome_judge(20)
        await judge_trace(outcome_task(), trace(), llm)
        messages = llm.ainvoke.call_args.args[0]
        self.assertIn("Score completed outcomes, not attempts.", messages[0].content)
        self.assertIn("<completion_contract>", messages[1].content[0].text)
        self.assertIn(REQUIREMENT, messages[1].content[0].text)
        self.assertIn("completed=null", messages[0].content)
        self.assertIn("inability to inspect", messages[0].content)

        old_judge = judge()
        result = await judge_trace(task(), trace(), old_judge)
        self.assertEqual(result["score"], 0.7)
        self.assertNotIn("completion_cap", result)
        old_messages = old_judge.ainvoke.call_args.args[0]
        self.assertNotIn("<completion_rule>", old_messages[0].content)
        self.assertNotIn("<completion_contract>", old_messages[1].content[0].text)
        self.assertNotIn(
            "completion_findings", findings_result_model(("A1", "A2")).model_fields
        )
        self.assertNotIn("<completion_rule>", FINDINGS_SYSTEM_PROMPT)


class ContractValidationTests(unittest.TestCase):
    def test_existing_200_tasks_remain_on_original_policy(self):
        tasks = load_tasks()
        self.assertEqual(len(tasks), 200)
        self.assertTrue(all(completion_contract(task) is None for task in tasks))

    def test_contract_requires_consistent_agent_visible_instructions(self):
        for field in ("confirmed_task", "rubric"):
            candidate = outcome_task()
            candidate[field] = "A1: Collect whatever is accessible. A2: Be honest."
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_findings_task(candidate)

    def test_malformed_contracts_are_rejected(self):
        good = outcome_task()["completion_contract"]
        invalid = [None, {}, {**good, "version": "other"}, {**good, "groups": []}]
        for value in (0, -1, True, 1.5, "25"):
            invalid.append(
                {**good, "groups": [{**good["groups"][0], "required": value}]}
            )
        invalid.append({**good, "groups": good["groups"] * 2})
        invalid.append({**good, "unexpected": "ignored"})
        for contract in invalid:
            with self.subTest(contract=contract), self.assertRaises(ValidationError):
                completion_contract({**outcome_task(), "completion_contract": contract})

    def test_contract_hash_changes_with_required_count(self):
        from completion_policy import completion_metadata

        first = completion_contract(outcome_task())
        changed = copy.deepcopy(outcome_task())
        changed["completion_contract"]["groups"][0]["required"] = 30
        second = completion_contract(changed)
        self.assertNotEqual(
            completion_metadata(first)["completion_contract_sha256"],
            completion_metadata(second)["completion_contract_sha256"],
        )
