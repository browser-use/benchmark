"""Main benchmark evaluation script."""

import asyncio
import base64, hashlib, json, traceback
from pathlib import Path
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from browser_use import Agent, Browser
from browser_use.llm import ChatBrowserUse

load_dotenv()

TASKS_FILE = Path(__file__).parent / "BU_Bench_V1.enc"
MAX_CONCURRENT = 5


def load_tasks() -> list[dict]:
    key = base64.urlsafe_b64encode(hashlib.sha256(b"BU_Bench_V1").digest())
    encrypted = base64.b64decode(TASKS_FILE.read_text())
    return json.loads(Fernet(key).decrypt(encrypted))


async def run_task(task: dict, semaphore: asyncio.Semaphore) -> dict:
    """Run a single task. Returns result dict with score (0 on failure)."""
    async with semaphore:
        try:
            task_id = task.get("task_id", "unknown")
            print(f"Running task: {task_id}")

            # To swap browser: replace with Browser(cdp_url=...) for other providers
            browser = Browser(use_cloud=True, cloud_timeout=30)

            # To swap model: replace ChatBrowserUse() with your LLM (e.g. ChatOpenAI, ChatAnthropic)
            # You can use any OpenAI API compatible model by changing base_url. You can use ollama too. See https://docs.browser-use.com/supported-models for info
            # agent = Agent(task=task["confirmed_task"], llm=ChatBrowserUse(), browser=browser)
            agent = Agent(task="Get the name of the top post on Hacker News", llm=ChatBrowserUse(), browser=browser) # DEBUG: Mock in a short task
            agent_history = await agent.run() # Closes browser automatically after run

            # TODO: Convert agent history to judge input (result, screenshots, trace)
            # TODO: Run judge on trace
            # TODO: Save task result to run_data/{run_name}/{task_id}.json

            return {"task_id": task_id, "score": 1, "history": agent_history}
        except Exception as e:
            error_type = type(e).__name__
            error_msg = f"{error_type}: {e}"
            print(f"Task {task.get('task_id', 'unknown')} failed: {error_msg}")
            return {"task_id": task.get("task_id"), "score": 0, "error": error_msg, "traceback": traceback.format_exc()}


async def main():
    tasks = load_tasks()[:1]  # First 1 task only for now
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    results = await asyncio.gather(*[run_task(t, semaphore) for t in tasks])

    # TODO: Aggregate scores and save to official_results/{run_name}.json
    print(f"Results: {results}")


if __name__ == "__main__":
    asyncio.run(main())
