/**
 * Browserbase Stagehand agent executor (client-side SDK path).
 *
 * Why client-side and not the hosted REST API:
 *   `api.stagehand.browserbase.com` is an alpha hosted endpoint running a
 *   Stagehand server build that predates the opus-4-7 temperature fix
 *   (Stagehand PRs #2006/#2018, shipped in stagehand-server-v3 v3.6.5 on
 *   May 6 2026). That endpoint silently rejects opus-4-7 with the
 *   "`temperature` is deprecated for this model" error from inside the
 *   Stagehand `fillForm` tool. Running the client SDK locally gives us
 *   whichever Stagehand version we pin in package.json, fix included.
 *
 *   This is also the path Browserbase tells customers to use for
 *   production (https://docs.stagehand.dev/v3/best-practices/deployments):
 *   embed the SDK in your backend, point it at Browserbase. The REST API
 *   is marketed for their Python SDK transport, not for scale-out.
 *
 * Joint system benchmarked: (Stagehand agent SDK + Browserbase cloud
 * browser + model). Same surface as the original .mjs example we built
 * for the .bcode workspace, just dispatched programmatically.
 *
 * Model routing: defaults to Browserbase Model Gateway (Stagehand
 * auto-routes through the gateway when only `apiKey` is set on the
 * constructor and no provider env key is present). The runner unsets
 * provider env keys before spawning this script when STAGEHAND_USE_GATEWAY
 * is "1" (default) so the SDK doesn't grab them out of process env.
 *
 * Env input (read at startup, all required unless noted):
 *   TASK_DESCRIPTION         the task string to run
 *   STAGEHAND_MODEL          gateway slug e.g. anthropic/claude-opus-4-7
 *   MAX_STEPS                int, default 25
 *   BROWSERBASE_API_KEY      required (for browser + gateway)
 *   BROWSERBASE_PROJECT_ID   required (Stagehand SDK still wants it)
 *   STAGEHAND_VERBOSE        int 0/1/2, default 1
 *
 * Stdout: exactly one JSON object -- the ExecutionResult-shaped dict the
 * Python wrapper reads. All progress / logs go to stderr.
 */

import { Stagehand } from "@browserbasehq/stagehand";

const MODEL = process.env.STAGEHAND_MODEL || "anthropic/claude-sonnet-4-6";
const MAX_STEPS = parseInt(process.env.MAX_STEPS || "25", 10);
const VERBOSE = parseInt(process.env.STAGEHAND_VERBOSE || "1", 10);

const SYSTEM_PROMPT =
  "You are a browser agent running inside an evaluation harness. " +
  "Solve the user's task by navigating and interacting with the live web.\n\n" +
  "When you finish, your final message MUST contain the concrete answer " +
  "to the task -- the actual names, numbers, list items, or values you " +
  "found. Do not paraphrase the answer as 'I extracted X' or 'I found the " +
  "data' -- write the data itself. For lists, write items one per line.";

function fail(msg, extra = {}) {
  // Emit an ExecutionResult-shaped object so the Python side records the
  // failure on the datapoint instead of raising -- matches the
  // "[browserbase_incomplete] ..." convention from the REST runner.
  const out = {
    final_result: `[browserbase_incomplete] ${msg}`,
    steps: [],
    screenshots_b64: [],
    num_steps: 0,
    duration_seconds: 0,
    cost: 0,
    error: msg,
    ...extra,
  };
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}

function formatStep(act, i) {
  // Stagehand 3.x agent action shape: { type, action?, reasoning?,
  // instruction?, pageUrl?, taskCompleted? }. We render one judge-readable
  // step per action, same as the REST runner's _format_steps.
  const parts = [`Step ${i}:`];
  if (act?.type) parts.push(`Type: ${act.type}`);
  if (act?.instruction) parts.push(`Instruction: ${act.instruction}`);
  if (act?.action) parts.push(`Action: ${act.action}`);
  if (act?.reasoning) parts.push(`Reasoning: ${act.reasoning}`);
  if (act?.pageUrl) parts.push(`URL: ${act.pageUrl}`);
  if (act?.taskCompleted) parts.push("TaskCompleted: true");
  return parts.join("\n");
}

async function main() {
  const task = process.env.TASK_DESCRIPTION;
  if (!task) fail("TASK_DESCRIPTION env var is required");
  for (const k of ["BROWSERBASE_API_KEY", "BROWSERBASE_PROJECT_ID"]) {
    if (!process.env[k]) fail(`missing env var: ${k}`);
  }

  process.stderr.write(
    `[browserbase-agent] model=${MODEL} maxSteps=${MAX_STEPS}\n`
  );

  const stagehand = new Stagehand({
    env: "BROWSERBASE",
    apiKey: process.env.BROWSERBASE_API_KEY,
    projectId: process.env.BROWSERBASE_PROJECT_ID,
    // With `model` on the constructor and no provider key on env, Stagehand
    // routes inference through the Browserbase Model Gateway. The Python
    // wrapper scrubs provider keys before spawning us when gateway mode is
    // requested (the default).
    model: MODEL,
    verbose: VERBOSE,
    disablePino: true,
    logger: (line) => {
      if ((line?.level ?? 1) > VERBOSE) return;
      const tag = line.level === 0 ? "ERR" : line.level === 2 ? "DBG" : "INF";
      const cat = line.category ? `[${line.category}] ` : "";
      process.stderr.write(`[stagehand:${tag}] ${cat}${line.message}\n`);
    },
  });

  const t0 = Date.now();
  try {
    await stagehand.init();
  } catch (err) {
    fail(`stagehand.init failed: ${err?.message || err}`);
  }

  const sessionId = stagehand.browserbaseSessionID;
  const recordingUrl = `https://browserbase.com/sessions/${sessionId}`;
  process.stderr.write(`[browserbase-agent] session=${sessionId}\n`);
  process.stderr.write(`[browserbase-agent] watch=${recordingUrl}\n`);

  const agent = stagehand.agent({ systemPrompt: SYSTEM_PROMPT });

  let result;
  let agentError = null;
  try {
    result = await agent.execute({
      instruction: task,
      maxSteps: MAX_STEPS,
    });
  } catch (err) {
    agentError = err?.stack || String(err);
    process.stderr.write(`[browserbase-agent] agent error: ${agentError}\n`);
  } finally {
    await stagehand.close().catch(() => {});
  }

  const durationSeconds = (Date.now() - t0) / 1000;

  if (agentError && !result) {
    fail(`agent.execute threw: ${agentError}`, {
      duration_seconds: durationSeconds,
      session_id: sessionId,
      recording_url: recordingUrl,
    });
  }

  const actions = Array.isArray(result?.actions) ? result.actions : [];
  const message = result?.message || "[browserbase_no_output]";
  const completed = !!result?.completed;
  const finalResult =
    completed || message.startsWith("[browserbase_")
      ? message
      : `[browserbase_incomplete] ${message}`;

  const out = {
    final_result: finalResult,
    steps: actions.map((a, i) => formatStep(a, i + 1)),
    screenshots_b64: [], // Stagehand agent.execute doesn't surface shots directly.
    num_steps: actions.length,
    duration_seconds: durationSeconds,
    // Token counts are in result.usage but Browserbase gateway pricing
    // isn't exposed per-token. Leave at 0 (matches the REST runner) until
    // we wire static prices through.
    cost: 0,
    session_id: sessionId,
    recording_url: recordingUrl,
  };
  process.stdout.write(JSON.stringify(out));
}

main().catch((err) => {
  process.stderr.write(`[browserbase-agent] fatal: ${err?.stack || err}\n`);
  fail(`fatal: ${err?.message || err}`);
});
