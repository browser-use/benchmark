# Consumer web task benchmark V9

This manifest describes 502 public-web tasks: 363 cart tasks and 139 public-information tasks. It is separate from BU Bench V2.1 and Stealth Bench V2.

The V9 source removes 11 task IDs from the previous 513-task set. `exclusions-v9.json` lists each removed ID and its reason. Fleet Farm has a corrected URL and task text. The original datasets and scores remain unchanged. Two additional country-specific exclusions remain unselected; none were invented to obtain a round task count.

## Current results

`results.json` contains sanitized task outcomes and their aggregate scores. Run `python consumer_bench/score.py` from the repository root to recalculate each score. All providers use the same retained task IDs. Missing results remain unknown. Unstarted tasks remain separate from failures. No missing score becomes zero.

Browser Use, the OpenAI browser runner, and Muse use historical V8 results filtered to V9 task IDs. They are not fresh V9 runs. The revised Fleet Farm task limits exact cross-version comparison. The selected Browser Use run was the best retained run, which introduces selection bias. The OpenAI runner identity does not establish a Dot run.

Instinct supplied a partial result table. Its later acknowledgement of continuation does not establish resumed execution. Its conservative self-check is a separate provider claim. Grok Bot and Dot have no matched complete result in this export.

These scores measure endpoint completion. They do not measure a general website block rate. The tasks come from a curated difficult set. Models, browser profiles, dates, solver policies, deadlines, and evidence review differ across runs. Muse's CSV review does not establish Root review of all original actions or runtime releases.

## Execution contract

The public `dataset-v9.json` is a task manifest with IDs, domains, endpoint types, and task hashes. It omits full prompts. The private runner package preserves the exact task and protocol text under the source dataset hash. Use one original attempt per task and one active task per domain. Use the runner's supported parallel limit. Keep the original first-request deadline at eight minutes. Record the first browser request and original task ID. Preserve unknown, stopped, failed, and accepted attempts without retry.

Use public pages only. Stop after the requested cart or public-information endpoint. Never purchase, start checkout, reserve, subscribe, create accounts, enter private data, or request codes. Follow the active tool policy for every CAPTCHA and binding action. General solver permission does not replace any required action-time confirmation.

Return all task outcomes, full results and failure CSV files, unstarted counts, original evidence, and configuration. Record successes over all planned tasks, cart successes over 363, and public-information successes over 139. Keep reported endpoints, evidence review, protocol deviations, page exposure, and website blocks separate.
