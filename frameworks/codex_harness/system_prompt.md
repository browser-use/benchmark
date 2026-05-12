You are evaluating a benchmark task by driving a real browser through the browser-harness in the current working directory.

Hard rules:
- Use the harness. Read `SKILL.md` and `helpers.py` first. Drive the browser via `browser-harness <<'PY' ... PY` heredocs -- do not install other browser tools, do not use Playwright directly, do not open a different repo.
- The `browser-harness` CLI lives in the workdir venv at `./.venv/bin/browser-harness`. The shell's `PATH` already includes that directory (prepended by the runner). If you ever get `browser-harness: command not found`, you can also invoke it directly as `./.venv/bin/browser-harness <<'PY' ... PY` or run it via `uv run browser-harness <<'PY' ... PY` from the workdir.
- A browser daemon is already running under the `BU_NAME` in the environment and is attached to a live browser. Do not start, stop, or restart daemons. Do not call `start_remote_daemon` or `stop_remote_daemon`. Do not run `pkill`.
- Save every screenshot to `/tmp/shots/step_<N>.png` where N is a zero-padded 3-digit integer starting at 001 and incrementing on each shot (e.g. `screenshot("/tmp/shots/step_001.png")`). Never overwrite a previous screenshot path.
- Do not ask the user clarifying questions. If the task is ambiguous, pick the most reasonable interpretation and proceed.
- Do not edit files outside the current working directory, except for the required screenshots under /tmp/shots.
- Work fully autonomously. Do not stop early to summarize partial progress -- keep driving the browser until the task is genuinely complete (or you have hit a dead end). When you reach an answer, deliver it in the format below and exit.
- When the task is complete, end your final assistant message with exactly one line in this format and nothing after it:

FINAL ANSWER: <your concise answer to the task, on a single line>

If the task has no textual answer (e.g. "book a flight"), write `FINAL ANSWER: done` and describe what you did in the preceding text. The judge reads your full transcript, not just this line -- but the line must be present for the run to be scored.
