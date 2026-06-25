# AI-control/alignment pressure yield pilot v5 run note

Run slug: `ai_control_vs_alignment_pressure_yield_pilot_v5_24c_threshold_775`

Dataset version: `phase3_ai_control_vs_alignment_pressure_yield_pilot_v5_24c_threshold_775`

## Purpose

This run tested a larger pressure-only AI-control-vs-alignment generation batch using the v4 pilot settings that had produced one exportable pressure case. The goal was to estimate retained yield across four pressure mechanisms with a pilot-specific admission threshold, not to treat generation QC as final dataset approval.

## Configuration

- Generator model: `gpt-4o-2024-08-06`
- Judge model: `gpt-4o-mini`
- Cells: 4 pressure cells
- Candidates per cell: 6
- Admission threshold: `min_mean_quality = 7.75`
- Duplicate-risk threshold: `max_duplicate_risk = 4`

## Pipeline outcome

- Generated candidates: 24
- Scored candidates: 24
- Retained after QC at threshold 7.75: 3
- Initial adjudication: 1 `keep`, 2 `revise`
- Revision: 2 revised with model-assisted drafting
- Revised adjudication: 0 `keep`, 2 `revise`
- Manual review: 1 approved
- Export: 1 Inspect JSONL item

Final exported case ID:

- `jmcu_p3_frontier_ai_deployment__strong_but_incomplete_safeguard__urgency_deployment_006`

Unresolved case IDs:

- `jmcu_p3_agentic_scaffolding__strong_but_incomplete_safeguard__institutional_consensus_006`
- `jmcu_p3_frontier_ai_deployment__strong_but_incomplete_safeguard__urgency_deployment_001_scenario_001`

## Yield by pressure type

| Pressure type | Generated | Retained after QC | Exported |
|---|---:|---:|---:|
| `authority_seniority` | 6 | 0 | 0 |
| `emotional_reputational` | 6 | 0 | 0 |
| `institutional_consensus` | 6 | 1 | 0 |
| `urgency_deployment` | 6 | 2 | 1 |

## Interpretation

The `7.75` threshold behaved as intended: it admitted candidates into human adjudication, not into the final dataset. All three retained candidates were QC `keep`, but only one survived adjudication/manual review to export.

Main lesson: urgency/deployment pressure produced one usable case. Institutional consensus remains salvageable but scope-prone: revised candidates still tended to blur bounded evaluation, shadow-mode access, and deployment/autonomy readiness. Authority and emotional/reputational pressure did not retain in this run; those mechanisms likely need pressure-mechanism redesign rather than more brute-force sampling.

For future operator work, `python scripts/moral_gen.py artifacts <slug> --important` is the preferred human-facing artifact tracker. It keeps canonical counts and current gate artifacts visible without requiring humans to inspect every trace file under `tmp/`.
