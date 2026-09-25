# Independent provider confirmation

Same frozen100 tasks, one new run per provider,700 scheduled attempts. Native configured proxies/solvers are retained; Browserbase is standard. Every provider used concurrency5, so this repeats the access protocol at a lower load than some original runs. Original two-repeat publication is unchanged.

| Provider | Original two-run average | Fresh confirmation | Unresolved /100 |
| --- | ---: | ---: | ---: |
| Browser Use Cloud | 90.5% | 90% | 1 |
| Browserbase standard | 78.5% | 78% | 0 |
| Kernel | 80.5% | 83% | 0 |
| Anchor | 82% | 85% | 0 |
| Hyperbrowser | 73.5% | 72% | 1 |
| Steel | 74.5% | 74% | 0 |
| Browserless | 77.5% | 83% | 3 |

Confirmed access uses all100 scheduled attempts per provider. Unresolved outcomes retain null scores and possible-access bounds in summary.json. Only missing measurements receive one retry; valid failures and uncertain rendered pages are preserved. Known judge/selection/product-profile limitations in the parent methodology still apply.
