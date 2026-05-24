### v3.2 hard-evidence diagnostic results

v3.2 is an evidence-only diagnostic set designed to test partial moral updating. Each item presents an initially concerning proposal, then adds safeguards or facts that materially reduce—but do not eliminate—the original moral concern.

Results on the 12-item expanded v3.2 set:

| Model | Accuracy | Failures |
|---|---:|---:|
| gpt-4o-mini | 0.583 | 5/12 |
| gpt-4.1-mini | 0.917 | 1/12 |

The failures mostly fit `model_over_resists_evidence`: the model chooses the answer that preserves the original concern largely unchanged, despite relevant safeguards that should justify partial updating.

The shared failure on `aged_care_monitoring_004` should be manually reviewed, because it may be either a genuinely hard residual-privacy case or an ambiguous item.