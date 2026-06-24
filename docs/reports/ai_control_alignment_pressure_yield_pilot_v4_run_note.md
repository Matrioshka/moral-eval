# AI-control/alignment pressure yield pilot v4 run note

Run slug: `ai_control_vs_alignment_pressure_yield_pilot_v4_threshold_775`

Dataset version: `phase3_ai_control_vs_alignment_pressure_yield_pilot_v4_threshold_775`

This was a pressure-only AI-control-vs-alignment yield pilot using a pilot-specific admission threshold of `min_mean_quality = 7.75`. The threshold was used only to admit candidates into human adjudication, not as final dataset approval.

## Outcome summary

- Generated candidates: 12
- Scored candidates: 12
- Admitted at threshold 7.75: 2
- Initial adjudication: 2 `revise`
- Revision: 2 revised with strong-model drafting
- Revised adjudication: 1 `keep`, 1 `revise`
- Manual review: 1 approved
- Export: 1 Inspect JSONL item

Final exported case ID:

- `jmcu_p3_shutdown_control_evaluation__strong_but_incomplete_safeguard__authority_seniority_002`

Unresolved case ID:

- `jmcu_p3_agentic_scaffolding__strong_but_incomplete_safeguard__institutional_consensus_001`

## Source patch

- `9643ffe` - Fix revised adjudication eligibility message

## Key lessons

- `7.75` is defensible as an admission-to-human-adjudication threshold for pressure-generation pilots, not as a final quality threshold.
- Strong-model revision can salvage some pressure candidates that are topically viable but need scope and pressure-isolation repair.
- Stale manual-review artefact names should not be used after revised adjudication. The current lifecycle should rely on `ready_for_manual_review.jsonl`, `manual_review_gate_template.csv`, `unresolved_for_manual_review.jsonl`, and `manual_review_preparation_summary.json` produced by `prepare_manual_review`.
