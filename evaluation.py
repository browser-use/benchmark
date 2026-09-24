"""Dataset and judge adapters for the public benchmark runner."""

import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from browser_use import ChatGoogle
from browser_use.llm import ChatOpenAI
from browser_use.llm.messages import ContentPartImageParam, ImageURL
from cryptography.fernet import Fernet

from findings_judge import (
    FILES_MAX_CHARS,
    FINAL_RESULT_MAX_CHARS,
    RUBRIC_MAX_CHARS,
    TASK_MAX_CHARS,
    TRAJECTORY_MAX_CHARS,
    WEBSITE_MAX_CHARS,
    construct_findings_judge_messages,
    findings_result_model,
)
from findings_judge import (
    score as score_findings,
)
from judge import JudgementResult, construct_judge_messages

DEFAULT_BENCHMARK = "BU_Bench_V2"
BENCHMARKS = (DEFAULT_BENCHMARK, "BU_Bench_V1", "Stealth_Bench_V1")
FINDINGS_MODEL = "gpt-5.6-luna"
LEGACY_MODEL = "gemini-2.5-flash"
MAX_IMAGES = 50
# The judge request contains base64 screenshot strings. Keep the encoded
# payload bounded as well as the image count so a long run cannot exceed the
# provider request/context limit merely because its PNGs are large.
MAX_SCREENSHOT_BYTES = 8_000_000


def validate_findings_task(task: dict) -> None:
    """Reject missing or malformed scoring metadata before executing an agent."""
    task_id = task.get("task_id", "unknown")
    rubric = task.get("rubric")
    weights = task.get("weights")
    if not isinstance(rubric, str) or not rubric.strip():
        raise ValueError(f"{task_id}: findings judging requires a nonempty rubric")
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"{task_id}: findings judging requires item weights")
    if (
        any(
            not isinstance(item, str)
            or not item
            or type(weight) is not int
            or weight <= 0
            for item, weight in weights.items()
        )
        or sum(weights.values()) != 100
    ):
        raise ValueError(f"{task_id}: weights must be positive integers totaling 100")
    if any(not re.search(rf"\b{re.escape(item)}\b", rubric) for item in weights):
        raise ValueError(f"{task_id}: a weighted item is absent from the rubric")


def load_tasks(benchmark: str = DEFAULT_BENCHMARK) -> list[dict]:
    """Decrypt in memory and normalize V2's id/task fields for the runner."""
    if benchmark not in BENCHMARKS:
        raise ValueError(f"Unknown benchmark: {benchmark}")
    path = Path(__file__).parent / f"{benchmark}.enc"
    key = base64.urlsafe_b64encode(hashlib.sha256(benchmark.encode()).digest())
    data = json.loads(Fernet(key).decrypt(base64.b64decode(path.read_text())))
    tasks = data["tasks"] if isinstance(data, dict) else data
    normalized = []
    for task in tasks:
        task = {
            **task,
            "task_id": task.get("task_id", task.get("id")),
            "confirmed_task": task.get("confirmed_task", task.get("task")),
        }
        if not task["task_id"] or not task["confirmed_task"]:
            raise ValueError("Every task must have an id and instruction")
        if benchmark == DEFAULT_BENCHMARK:
            validate_findings_task(task)
        normalized.append(task)
    return normalized


def create_judge(benchmark=DEFAULT_BENCHMARK, model=None, reasoning_effort="xhigh"):
    if benchmark == DEFAULT_BENCHMARK:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is required for the V2 findings judge")
        return ChatOpenAI(
            model=model or FINDINGS_MODEL,
            api_key=os.environ["OPENAI_API_KEY"],
            reasoning_effort=reasoning_effort,
            # The pinned SDK otherwise defaults to 4096, including reasoning.
            max_completion_tokens=32768,
            temperature=None,
            frequency_penalty=None,
            timeout=300,
            max_retries=2,
        )
    if benchmark not in BENCHMARKS:
        raise ValueError(f"Unknown benchmark: {benchmark}")
    if not os.getenv("GOOGLE_API_KEY"):
        raise ValueError("GOOGLE_API_KEY is required for the legacy binary judge")
    return ChatGoogle(model=model or LEGACY_MODEL, api_key=os.environ["GOOGLE_API_KEY"])


def judge_config(benchmark: str, llm) -> dict:
    findings = benchmark == DEFAULT_BENCHMARK
    endpoint = getattr(llm, "base_url", None) or (
        os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        if findings
        else "https://generativelanguage.googleapis.com"
    )
    endpoint_host = urlparse(str(endpoint)).hostname
    return {
        "type": "findings" if findings else "legacy_binary",
        "adapter_version": "2.1.1" if findings else "legacy-v1",
        "adapter_source_sha256": hashlib.sha256(
            Path(__file__).read_bytes()
        ).hexdigest(),
        "model": llm.model,
        "endpoint_host": endpoint_host,
        "reasoning_effort": getattr(llm, "reasoning_effort", None)
        if findings
        else None,
        "max_completion_tokens": getattr(llm, "max_completion_tokens", None),
        "max_images": MAX_IMAGES if findings else 10,
        "max_screenshot_bytes": MAX_SCREENSHOT_BYTES if findings else None,
        "image_selection": "evenly_spaced_unique" if findings else "last_unique",
        "screenshot_timing": "before_action",
        "judge_source_sha256": hashlib.sha256(
            Path(__file__)
            .with_name("findings_judge.py" if findings else "judge.py")
            .read_bytes()
        ).hexdigest(),
    }


def select_screenshots(
    images: list[str],
    max_images: int = MAX_IMAGES,
    max_bytes: int = MAX_SCREENSHOT_BYTES,
) -> list[str]:
    """Deduplicate and sample frames under count and encoded-byte budgets.

    The first and last unique frames are mandatory. Other frames are selected
    at even intervals where they fit, then filled from the remaining frames in
    chronological order. A budget error is explicit when even the mandatory
    frames cannot fit; silently dropping the boundary evidence would make a
    score incomparable.
    """
    unique = list(dict.fromkeys(images))
    if not unique or max_images <= 0:
        return []
    if max_bytes <= 0:
        raise ValueError("screenshot byte budget must be positive")
    if any(not isinstance(image, str) for image in unique):
        raise ValueError("screenshots must be base64 strings")

    target_count = min(max_images, len(unique))
    mandatory = {0}
    if target_count > 1:
        mandatory.add(len(unique) - 1)
    sizes = [len(image.encode("ascii")) for image in unique]
    used = sum(sizes[index] for index in mandatory)
    if used > max_bytes:
        raise ValueError("screenshot byte budget cannot retain first and last frames")

    selected = set(mandatory)
    if target_count > 1:
        candidates = [
            round(i * (len(unique) - 1) / (target_count - 1))
            for i in range(target_count)
        ]
    else:
        candidates = [0]
    for index in candidates + list(range(len(unique))):
        if index in selected or len(selected) >= target_count:
            continue
        if used + sizes[index] <= max_bytes:
            selected.add(index)
            used += sizes[index]
    return [unique[index] for index in sorted(selected)]


def evidence_truncation_sections(task: dict, trace: dict) -> list[str]:
    """Return prompt sections that the builder would clip or omit."""
    sections = []
    values = (
        ("task", task.get("confirmed_task", ""), TASK_MAX_CHARS),
        ("website", task.get("website") or "", WEBSITE_MAX_CHARS),
        ("rubric", task.get("rubric", ""), RUBRIC_MAX_CHARS),
        ("trajectory", "\n".join(trace.get("agent_steps") or []), TRAJECTORY_MAX_CHARS),
        ("final_result", trace.get("final_result") or "", FINAL_RESULT_MAX_CHARS),
        ("files", trace.get("output_files_text") or "", FILES_MAX_CHARS),
    )
    for name, value, limit in values:
        if len(value) > limit:
            sections.append(name)
    return sections


def _incomplete_result(
    sections: list[str],
    judgement=None,
    diagnostic_score=None,
    diagnostic_raw_score=None,
) -> dict:
    """Represent evidence that cannot support a headline score."""
    return {
        "score": None,
        "raw_score": None,
        "evidence_incomplete": True,
        "evidence_incomplete_sections": sections,
        "diagnostic_score": diagnostic_score,
        "diagnostic_raw_score": diagnostic_raw_score,
        "judgement": judgement.model_dump() if judgement is not None else None,
    }


def _validate_findings_coverage(judgement, item_ids: tuple[str, ...]) -> None:
    """Fail closed if the structured judge omits or repeats a rubric item."""
    counts = {item: 0 for item in item_ids}
    for finding in judgement.findings:
        if finding.item not in counts:
            raise ValueError(f"Judge returned unknown rubric item: {finding.item}")
        counts[finding.item] += 1
    missing = [item for item, count in counts.items() if count == 0]
    duplicate = [item for item, count in counts.items() if count > 1]
    if missing or duplicate or len(judgement.findings) != len(item_ids):
        details = []
        if missing:
            details.append(f"missing={missing}")
        if duplicate:
            details.append(f"duplicate={duplicate}")
        raise ValueError(
            "Judge findings must contain exactly one item each ("
            + ", ".join(details)
            + ")"
        )


async def judge_trace(
    task: dict, trace: dict, llm, benchmark=DEFAULT_BENCHMARK
) -> dict:
    """Judge once against the full rubric; calculate weighted scores in code."""
    if benchmark == DEFAULT_BENCHMARK:
        validate_findings_task(task)
        truncations = evidence_truncation_sections(task, trace)
        # Keep the judging question intact. Clipped agent evidence is still
        # assessable when the remaining material supports the item findings.
        hard_truncations = [
            section for section in truncations if section in {"task", "rubric"}
        ]
        if hard_truncations:
            return _incomplete_result(hard_truncations)
        images = [
            ContentPartImageParam(
                image_url=ImageURL(
                    url=f"data:image/png;base64,{image}", media_type="image/png"
                )
            )
            for image in select_screenshots(
                trace["screenshots_b64"], max_bytes=MAX_SCREENSHOT_BYTES
            )
        ]
        messages = construct_findings_judge_messages(
            task=task["confirmed_task"],
            rubric=task["rubric"],
            task_id=task["task_id"],
            website=task.get("website"),
            final_result=trace["final_result"],
            agent_steps=trace["agent_steps"],
            output_files_text=trace.get("output_files_text"),
            # Browser Use's history images precede actions. Leave step labels
            # unset rather than claiming these were captured after the action.
            screenshots_b64=images,
            screenshot_timing="before",
        )
        schema = findings_result_model(tuple(task["weights"]))
    else:
        messages = construct_judge_messages(
            task=task["confirmed_task"],
            final_result=trace["final_result"],
            agent_steps=trace["agent_steps"],
            ground_truth=task.get("answer"),
            screenshots_b64=trace["screenshots_b64"],
        )
        schema = JudgementResult

    response = await llm.ainvoke(messages, output_format=schema)
    if benchmark == DEFAULT_BENCHMARK and getattr(
        response, "stop_reason", None
    ) not in (None, "stop"):
        raise ValueError(f"Judge did not finish: {response.stop_reason}")
    judgement = schema.model_validate(response.completion)
    if benchmark == DEFAULT_BENCHMARK:
        _validate_findings_coverage(judgement, tuple(task["weights"]))
        scoring = score_findings(
            task,
            judgement,
            agent_texts=[
                trace["final_result"],
                *trace["agent_steps"],
                trace.get("output_files_text") or "",
            ],
        )
        scoring["raw_score"] = scoring["earned_weight"] / sum(task["weights"].values())
        incomplete_sections = evidence_truncation_sections(task, trace)
        if incomplete_sections:
            scoring["evidence_clipped_sections"] = incomplete_sections
        incomplete_items = [
            finding.item
            for finding in judgement.findings
            if finding.status == "not_assessable"
            and finding.not_assessable_reason == "missing_evidence"
        ]
        if incomplete_items:
            diagnostic_score = scoring["score"]
            diagnostic_raw_score = scoring["raw_score"]
            return {
                **scoring,
                **_incomplete_result(
                    incomplete_sections,
                    judgement=judgement,
                    diagnostic_score=diagnostic_score,
                    diagnostic_raw_score=diagnostic_raw_score,
                ),
                "evidence_incomplete_items": incomplete_items,
            }
    else:
        scoring = {
            "score": float(judgement.verdict),
            "raw_score": float(judgement.verdict),
            "verdict": judgement.verdict,
        }
    return {**scoring, "judgement": judgement.model_dump()}
