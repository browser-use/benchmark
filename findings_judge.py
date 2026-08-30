"""Findings judge: the scoring method for BU Bench V2.

Published for transparency. This is the prompt and the scoring arithmetic used
to produce the reported results; it is NOT wired into run_eval.py, and the LLM
call plumbing is not included. Treat it as the specification, not a runnable
verifier.

The judge emits one finding per rubric item (met / violated / not_assessable,
each with evidence) and never emits a score. Valuation happens in code from the
task's weights, so re-weighting a rubric never requires re-judging a run.

Each BU Bench V2 task carries its own `rubric` and `weights` inline, and both
are frozen: `weights` keys are exactly the item ids the rubric defines, and they
sum to 100.

Two divergences from the internal implementation, both infrastructure rather
than method:
  - image selection: internally, screenshots are fitted to a byte budget shared
    with the prompt text. Here they are passed through in order.
  - the LLM call, retries, and tracing are omitted.
The system prompt, the section layout, the truncation caps, and the scoring are
identical to what produced the published numbers.
"""

from typing import Literal

from pydantic import BaseModel, create_model
from browser_use.llm.messages import (
	BaseMessage,
	ContentPartImageParam,
	ContentPartTextParam,
	SystemMessage,
	UserMessage,
)

FINDINGS_SYSTEM_PROMPT = """
<role>
You are a judge for browser-using agents.
You receive one agent run and one rubric for the task.
The rubric defines items. You report one finding per item, plus observations and flags.
You do not compute a score. You do not edit the rubric.
</role>

<input>
You receive these sections:
<task>: the user's task.
<website>: the main website, or 'No website provided'.
<rubric>: the rubric for this task. It lists the items, the source facts, and the rulings.
<agent_trajectory>: the agent's steps, numbered [step N]. Long tool results are clipped in the
middle, keeping the start and the end; each omission is marked inline.
<final_result>: the text the agent returned to the user.
<output_files>: files the agent saved as deliverables, if any.
<screenshots>: images captured by the harness after browser actions. Labels tie each image to a step. The agent does not choose or control these images. If a step's claim and its screenshot disagree, trust the screenshot.
</input>

<rubric_rule>
The rubric is correct. Its facts were verified against the live sources on the date it states.
Trust the rubric over the agent's claims. Apply its items and rulings exactly.
Exception: if the run's own evidence shows the site changed after the rubric's date, grade against the run's evidence and state this in the finding.
</rubric_rule>

<evidence_rule>
An item is `met` only when the deliverable content is corroborated by trace evidence: page content in screenshots, tool results, or data the trajectory produced.
Correct-looking data with no extraction evidence is not met. Models can recite public data from memory. Never credit it.
</evidence_rule>

<task_reading_rule>
Before the findings, reconstruct in agent_task_reading how THIS agent read the task: which sources it treated as required, what deliverable shape and semantics it adopted, and what it treated as optional or out of scope.
Derive the reading from what the agent DID. Quote assumptions the agent stated; mark the rest as inferred from behavior.
Describe the reading, do not judge it. This field is never scored. Keep it under 120 words.
</task_reading_rule>

<findings_rule>
Report exactly one finding for every item in the rubric, in rubric order. Never skip an item.
Write the evidence first, then the status.
- met: every requirement inside the item holds, with corroborating evidence. Cite the steps or values that corroborate it.
- violated: name the entries or values that fail and the source evidence they contradict, with an occurrence list.
- not_assessable: the evidence YOU cannot see (clipped file, missing section). Say exactly what was missing. It is not a soft violated.
An item is met only if it is flawless over its whole scope. Do not average within an item. Do not let one item's failure change another item's finding.
</findings_rule>

<observations_rule>
Report anything true and relevant that no item covers: site changes, novel failure shapes, deliverable oddities.
Observations are never scored. Do not restate item findings or the task reading here. An empty list is fine.
</observations_rule>

<flags>
Set these independently of the findings:
- infra_error: the run died from infrastructure outside the agent's control (browser or tunnel death, harness crash, site down for everyone). Anti-bot walls and login walls are not infra errors.
- pii_present: the run involved real personal data beyond the standard test persona.
- reward_hacking_suspected: the agent tried to game the task or the judging process, including fabricating deliverable content.
- flag_notes: one or two sentences when any flag is set, else null.
</flags>
"""

# Caps applied per section before the prompt is assembled. Trajectories are the
# dominant cost; long tool results are clipped in the middle so the start and
# the end of each survive.
TASK_MAX_CHARS = 40_000
WEBSITE_MAX_CHARS = 4_000
RUBRIC_MAX_CHARS = 100_000
FINAL_RESULT_MAX_CHARS = 100_000
TRAJECTORY_MAX_CHARS = 700_000
FILES_MAX_CHARS = 600_000

_MODEL_CACHE: dict[tuple[str, ...], type[BaseModel]] = {}


def findings_result_model(item_ids: tuple[str, ...]) -> type[BaseModel]:
	"""Structured-output schema with the item enum pinned to THIS task's rubric.

	Pinning the enum to the task's own item ids is what stops the judge from
	inventing an item or silently renaming one: an id outside the rubric fails
	schema validation rather than scoring zero unnoticed.
	"""
	if item_ids not in _MODEL_CACHE:
		finding = create_model(
			'Finding',
			item=(Literal[item_ids], ...),
			evidence=(str, ...),
			status=(Literal['met', 'violated', 'not_assessable'], ...),
		)
		_MODEL_CACHE[item_ids] = create_model(
			'FindingsResult',
			agent_task_reading=(str, ...),
			findings=(list[finding], ...),
			observations=(list[str], ...),
			infra_error=(bool, ...),
			pii_present=(bool, ...),
			reward_hacking_suspected=(bool, ...),
			flag_notes=(str | None, ...),
		)
	return _MODEL_CACHE[item_ids]


def _truncate(text: str, limit: int) -> str:
	"""Clip the middle, keeping the start and the end, and mark the omission."""
	if len(text) <= limit:
		return text
	half = limit // 2
	return f'{text[:half]}\n... [{len(text) - limit} characters omitted] ...\n{text[-half:]}'


def construct_findings_judge_messages(
	task: str,
	rubric: str,
	final_result: str,
	agent_steps: list[str],
	screenshots_b64: list[ContentPartImageParam],
	task_id: str | None = None,
	website: str | None = None,
	output_files_text: str | None = None,
	screenshot_steps: list[int] | None = None,
) -> list[BaseMessage]:
	if screenshot_steps is None:
		trajectory = '\n'.join(agent_steps)
		screenshots_note = '{n} screenshots from execution are attached below in chronological order.'
	else:
		# Number the steps so screenshot labels ([step N]) can be located.
		trajectory = '\n'.join(f'[step {i}] {s}' for i, s in enumerate(agent_steps, start=1))
		screenshots_note = (
			'{n} screenshots are attached below in chronological order. They were captured '
			'automatically by the harness immediately after browser actions (not chosen by the '
			'agent); each is labeled with the trajectory step it follows. Identical consecutive '
			'frames were removed.'
		)

	# Only frameworks that collect an outputs/ dir produce this section; None
	# omits it entirely so the prompt is unchanged for everything else.
	if output_files_text is None:
		output_files_section = ''
	else:
		output_files_section = f"""
<output_files>
{_truncate(output_files_text, FILES_MAX_CHARS)}
</output_files>
"""

	text_sections = f"""
<task>
{_truncate(task, TASK_MAX_CHARS) or 'No task provided'}
</task>

<website>
{_truncate(website or '', WEBSITE_MAX_CHARS) or 'No website provided'}
</website>

<rubric_path>
rubrics/{task_id}.md
</rubric_path>

<rubric>
{_truncate(rubric, RUBRIC_MAX_CHARS) or 'No rubric exists yet.'}
</rubric>

<agent_trajectory>
{_truncate(trajectory, TRAJECTORY_MAX_CHARS) or 'No agent trajectory provided'}
</agent_trajectory>

<final_result>
{_truncate(final_result, FINAL_RESULT_MAX_CHARS) or 'No final result provided'}
</final_result>
{output_files_section}"""

	user_prompt = f"""{text_sections}
<screenshots>
{screenshots_note.format(n=len(screenshots_b64))}
</screenshots>
"""

	content_parts: list[ContentPartTextParam | ContentPartImageParam] = [ContentPartTextParam(text=user_prompt)]
	for index, image in enumerate(screenshots_b64, start=1):
		step = screenshot_steps[index - 1] if screenshot_steps else None
		if step is None:
			label = f'Screenshot {index} of {len(screenshots_b64)}. Chronological order.'
		else:
			label = f'Screenshot {index} of {len(screenshots_b64)}, captured immediately after [step {step}].'
		content_parts.append(ContentPartTextParam(text=label))
		content_parts.append(image)

	return [
		SystemMessage(content=FINDINGS_SYSTEM_PROMPT),
		UserMessage(content=content_parts),
	]


def score(task: dict, judgement: BaseModel, agent_texts: list[str] | None = None) -> dict:
	"""Turn findings into a score. The LLM never sees weights and never scores.

	score = met weight / total weight. An item missing from the judge output
	earns nothing, and not_assessable earns nothing -- it means the evidence was
	unreadable, which is not partial credit.
	"""
	weights: dict[str, int] = task['weights']

	# Canary tripwire. Every rubric carries a token that never appears in task
	# text, so an agent can only reproduce it by reading the answer key. This is
	# deterministic and independent of the LLM judge.
	canary = task.get('canary')
	canary_leak = bool(canary) and any(canary in text for text in (agent_texts or []))

	# Duplicate findings for one item resolve worst-wins, so a stray second
	# "met" can never overwrite a "violated".
	severity = {'met': 0, 'not_assessable': 1, 'violated': 2}
	status: dict[str, str] = {}
	for f in judgement.findings:
		if f.item not in status or severity[f.status] > severity[status[f.item]]:
			status[f.item] = f.status

	earned = sum(w for i, w in weights.items() if status.get(i) == 'met')
	verdict = all(status.get(i) == 'met' for i in weights)
	rh = judgement.reward_hacking_suspected or canary_leak
	return {
		'score': 0.0 if rh else earned / sum(weights.values()),
		'verdict': False if rh else verdict,
		'earned_weight': earned,
		'rh_zeroed': rh,
		'canary_leak': canary_leak,
		'missing_items': [i for i in weights if i not in status],
	}
