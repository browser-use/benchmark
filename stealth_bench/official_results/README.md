# Recorded results

100 frozen tasks × two repetitions × 12 configurations = 2,400 scheduled original attempts. The dates, exact configuration options, source hashes and account limitations are recorded in the files below. Every reported rate retains 200 logical attempts per configuration.

| Configuration | R1 | R2 | Pooled access | Unresolved | Possible access |
| --- | ---: | ---: | ---: | ---: | ---: |
| Browser Use Cloud | 89% | 92% | 90.5% | 0/200 | 90.5% |
| Browser Use Cloud · solver off | 87% | 81% | 84% | 0/200 | 84% |
| Anchor | 85% | 79% | 82% | 0/200 | 82% |
| Kernel | 79% | 82% | 80.5% | 0/200 | 80.5% |
| Browserbase · standard | 80% | 77% | 78.5% | 1/200 | 79% |
| Browserless | 77% | 78% | 77.5% | 17/200 | 86% |
| Steel | 74% | 75% | 74.5% | 1/200 | 75% |
| Hyperbrowser | 73% | 74% | 73.5% | 2/200 | 74.5% |
| Chromium headful + US proxy | 15% | 13% | 14% | 0/200 | 14% |
| Chromium headful | 10% | 10% | 10% | 0/200 | 10% |
| Chromium headless | 3% | 3% | 3% | 0/200 | 3% |
| Chromium headless + US proxy | 2% | 2% | 2% | 0/200 | 2% |

- `summary.json`: pooled and per-repeat access, failure classes and unresolved bounds.
- `attempts.jsonl`: 2,400 effective model-judged outcomes, including the one permitted retry where used.
- `original-attempts.jsonl`: all 2,400 original outcomes; no cancellation or missing acquisition is silently dropped.
- `retry-attempts.jsonl`: the one-time missing-measurement retries, joinable by configuration, repetition and task ID. A valid failure or uncertain rendered page is never retried.
- `judge-sensitivity.json`: separate conservative bounds for identified visual-audit ambiguities. These do not replace the frozen judge scores and are not confidence intervals.
- `provenance.json`: pinned dataset, implementation, runner and requested provider settings. Browser version and viewport differences are product-configuration limitations.

Uncertain and acquisition/capture/judge errors have `score: null`. They are not bot blocks. The headline is confirmed model-judged access divided by all scheduled attempts; the possible-access bound adds unresolved observations. `retry_used` remains true even if the permitted retry is still unresolved.

Browserbase Verified could not be acquired on the existing account and has no website score. Superseded Browserless defaults and acquisition-only preflights are diagnostics, excluded as entire configurations. The corrected Browserless first repetition preserves 19 valid observations and retries 81 missing measurements at the documented five-session cap; repetition two covers all 100 tasks at that cap.

Raw page text/screenshots, provider session identifiers, exact egress addresses, customer messages and private trace URLs are intentionally absent. See the [methodology](../README.md) before using these results.
