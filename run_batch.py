"""Run a batch of benchmark tasks. Used by GitHub Actions runners."""

import os

import argparse
import asyncio
import json
from dotenv import load_dotenv
from browser_use import ChatGoogle
from browser_use.llm import ChatBrowserUse, ChatOpenAI, ChatAnthropic
from run_eval import load_tasks, run_task

load_dotenv()

def interleave(tasks: list) -> list:
    """Reorder 100 tasks, 20 per section to balance difficulty."""
    reordered = []
    for i in range(20):
        for d in range(5): 
            reordered.append(tasks[d * 20 + i])
    return reordered


MODELS = {
    "ChatBrowserUse-1": lambda: ChatBrowserUse(model="bu-1-0"),
    "ChatBrowserUse-2": lambda: ChatBrowserUse(model="bu-2-0"),

    "gpt-5-mini": lambda: ChatOpenAI(model="gpt-5-mini", api_key=os.getenv("OPENAI_API_KEY")),
    "gpt-5.1-codex-mini": lambda: ChatOpenAI(model="gpt-5.1-codex-mini", api_key=os.getenv("OPENAI_API_KEY")),
    "gpt-5": lambda: ChatOpenAI(model="gpt-5", api_key=os.getenv("OPENAI_API_KEY")),

    "claude-3-5-haiku": lambda: ChatAnthropic(model="claude-3-5-haiku", api_key=os.getenv("ANTHROPIC_API_KEY")),
    "claude-4-5-haiku": lambda: ChatAnthropic(model="claude-4-5-haiku", api_key=os.getenv("ANTHROPIC_API_KEY")),
    "claude-4-5-sonnet": lambda: ChatAnthropic(model="claude-4-5-sonnet", api_key=os.getenv("ANTHROPIC_API_KEY")),
    "claude-4-5-opus": lambda: ChatAnthropic(model="claude-4-5-opus", api_key=os.getenv("ANTHROPIC_API_KEY")),

    "gemini-2.5-flash-lite": lambda: ChatGoogle(model="gemini-2.5-flash-lite", api_key=os.getenv("GOOGLE_API_KEY")),
    "gemini-2.5-flash": lambda: ChatGoogle(model="gemini-2.5-flash", api_key=os.getenv("GOOGLE_API_KEY")),
    "gemini-3-flash-preview": lambda: ChatGoogle(model="gemini-3-flash-preview", api_key=os.getenv("GOOGLE_API_KEY")),
    "gemini-3-pro-preview": lambda: ChatGoogle(model="gemini-3-pro-preview", api_key=os.getenv("GOOGLE_API_KEY")),
}


async def run_batch(model_name: str, start: int, end: int, parallel: int = 3, tracking_id: str = None, run_start: str = None) -> dict:
    """Run tasks[start:end] with given model. Returns results summary."""
    tasks = interleave(load_tasks())[start:end]
    llm = MODELS[model_name]()
    sem = asyncio.Semaphore(parallel)
    
    results = await asyncio.gather(*[run_task(t, sem, llm=llm, run_data_dir=None) for t in tasks])
    
    # Aggregate
    return {
        "tracking_id": tracking_id,
        "model": model_name,
        "start": start,
        "end": end,
        "run_start": run_start,
        "tasks_completed": len(results),
        "tasks_successful": sum(1 for r in results if r.get("score") == 1),
        "total_steps": sum(r.get("steps", 0) for r in results),
        "total_duration": sum(r.get("duration", 0) for r in results),
        "total_cost": sum(r.get("cost", 0) for r in results),
        "task_results": [{"task_id": r["task_id"], "score": r["score"], "steps": r.get("steps", 0), "duration": r.get("duration", 0), "cost": r.get("cost", 0)} for r in results]
    }


def main():
    parser = argparse.ArgumentParser(description="Run a batch of benchmark tasks")
    parser.add_argument("--model", required=True, choices=list(MODELS.keys()), help="Model to use")
    parser.add_argument("--start", type=int, required=True, help="Start task index (inclusive)")
    parser.add_argument("--end", type=int, required=True, help="End task index (exclusive)")
    parser.add_argument("--parallel", type=int, default=3, help="Max concurrent tasks (default: 3)")
    parser.add_argument("--tracking-id", required=True, help="UUID for orchestrator matching")
    parser.add_argument("--run-start", required=True, help="Run start timestamp for aggregation")
    parser.add_argument("--output", required=True, help="Output file path for results JSON")
    args = parser.parse_args()
    
    result = asyncio.run(run_batch(args.model, args.start, args.end, args.parallel, args.tracking_id, args.run_start))
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
