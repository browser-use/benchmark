/**
 * Stagehand agent executor.
 *
 * Reads TASK_DESCRIPTION and BROWSER from env.
 * Runs the Stagehand agent and prints a JSON result to stdout.
 *
 * Expected stdout format:
 * {
 *   "final_result": "...",
 *   "steps": ["step 1", "step 2", ...],
 *   "screenshots_b64": ["base64...", ...],
 *   "num_steps": 10,
 *   "duration_seconds": 45.2,
 *   "cost": 0.05
 * }
 */

// TODO: Implement Stagehand execution
// const { Stagehand } = require("@browserbasehq/stagehand");

async function main() {
  const taskDescription = process.env.TASK_DESCRIPTION;
  const browser = process.env.BROWSER || "browserbase";

  if (!taskDescription) {
    console.error("TASK_DESCRIPTION env var is required");
    process.exit(1);
  }

  const startTime = Date.now();

  // TODO: Initialize Stagehand with appropriate env (BROWSERBASE or LOCAL)
  // const stagehand = new Stagehand({
  //   env: browser === "browserbase" ? "BROWSERBASE" : "LOCAL",
  //   modelName: "anthropic/claude-sonnet-4-20250514",
  //   modelClientOptions: { apiKey: process.env.ANTHROPIC_API_KEY },
  // });
  // await stagehand.init();
  //
  // const page = stagehand.context.pages()[0];
  // const agent = stagehand.agent({ modelName: "anthropic/claude-sonnet-4-20250514" });
  // const result = await agent.execute({ instruction: taskDescription });
  //
  // await stagehand.close();

  const durationSeconds = (Date.now() - startTime) / 1000;

  // TODO: Map Stagehand result to standard format
  const output = {
    final_result: "NOT IMPLEMENTED",
    steps: [],
    screenshots_b64: [],
    num_steps: 0,
    duration_seconds: durationSeconds,
    cost: 0,
  };

  // Print JSON to stdout for the Python wrapper to parse
  console.log(JSON.stringify(output));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
