# Moral Sycophancy / Moral Reasoning Integrity Eval

Small Inspect eval suite for testing whether language models preserve moral reasoning integrity under user pressure.

The project currently includes:

- a recognition baseline;
- an integrity MCQ eval;
- a behavioural free-response eval;
- evidence-strength datasets that test whether models distinguish strong evidence from weak, cosmetic, or irrelevant reassurance.

## What this project tests

The project is not mainly testing whether a model knows common moral rules. It tests whether a model maintains epistemic and moral discipline when a user supplies pressure, reassurance, or new information.

The target behaviour is moral reasoning integrity:

- update when new evidence genuinely changes the moral situation;
- preserve residual concerns when safeguards are partial;
- reject irrelevant reassurance as morally non-resolving;
- avoid premature moral closure when the user wants the concern to go away.

A model shows moral sycophancy when it shifts towards the user's preferred moral conclusion for reasons not justified by the evidence supplied.

## Current eval components

### Recognition baseline

The recognition baseline asks the model to identify which of two answers is sycophantic.

Relevant files:

- `data/moral_sycophancy_recognition_seed_v*.jsonl`
- `src/moral_sycophancy_eval/recognition.py`

Run:

```bash
inspect eval src/moral_sycophancy_eval/recognition.py --model openai/gpt-4o-mini
```

### Integrity MCQ eval

The integrity MCQ eval tests whether a model selects the answer that best preserves moral reasoning integrity.

Relevant files:

- `data/moral_reasoning_integrity_seed_v*.jsonl`
- `src/moral_sycophancy_eval/integrity.py`

Run:

```bash
inspect eval src/moral_sycophancy_eval/integrity.py --model openai/gpt-4o-mini -T dataset_version=v3
```

### Behavioural free-response eval

The behavioural eval places the model directly in the pressured interaction and asks it to answer in its own words.

Relevant files:

- `src/moral_sycophancy_eval/behaviour.py`
- `src/moral_sycophancy_eval/export_behaviour_outputs.py`
- `src/moral_sycophancy_eval/summarise_manual_scores.py`

Run the canonical evidence-strength dataset:

```bash
inspect eval src/moral_sycophancy_eval/behaviour.py --model openai/gpt-4o-mini -T dataset_version=v3_evidence_strength_v1
```

Run the trap-expansion diagnostic dataset:

```bash
inspect eval src/moral_sycophancy_eval/behaviour.py --model openai/gpt-4o-mini -T dataset_version=v3_evidence_strength_trap_expansion_v1
```

Use whichever model/provider string is configured in your Inspect environment.

## Current behavioural datasets

### Canonical evidence-strength dataset

`v3_evidence_strength_v1` is the current frozen canonical behavioural dataset.

File:

`data/moral_reasoning_integrity_behaviour_evidence_strength_v1.jsonl`

It contains 18 samples: 6 base moral-risk cases, each with 3 follow-up strengths:

- `strong_evidence_update`
- `weak_or_cosmetic_safeguard`
- `irrelevant_reassurance`

This dataset measures broad evidence-strength calibration: whether a model updates appropriately when genuinely relevant safeguards are introduced, while preserving residual moral concerns when the follow-up evidence is weak, cosmetic, or irrelevant.

Manual audit files:

- `docs/failure_audits/v3_behaviour_evidence_strength_manual_audit.md`
- `docs/failure_audits/v3_behaviour_evidence_strength_v1_manual_scores.csv`
- `docs/failure_audits/v3_behaviour_evidence_strength_v1_score_summary.md`

### Trap expansion diagnostic dataset

`v3_evidence_strength_trap_expansion_v1` is a separate diagnostic stress test.

File:

`data/moral_reasoning_integrity_behaviour_evidence_strength_trap_expansion_v1.jsonl`

It probes harder cases involving weak procedural safeguards and irrelevant/cosmetic/reputational reassurance. It should not currently be treated as a replacement for the canonical dataset.

The trap expansion tests whether models over-credit:

- reputational reassurance;
- managerial review;
- cosmetic or affective design changes;
- comfort or usability improvements;
- vague procedural safeguards;
- weak oversight language that does not resolve the original moral concern.

Manual audit files:

- `docs/failure_audits/v3_behaviour_evidence_strength_trap_expansion_v1_manual_scores.csv`
- `docs/failure_audits/v3_behaviour_evidence_strength_trap_expansion_v1_score_summary.md`

## Preliminary finding

The clearest finding is not a large aggregate score gap between strong, weak, and irrelevant follow-ups. The clearer signal is a difference in failure type.

When follow-up evidence is strong, models usually update in the right direction. Their failures often involve under-specifying residual moral concerns: they recognise that the new evidence matters, but compress the remaining risk into vague caution.

When follow-up evidence is weak, cosmetic, reputational, managerial, or irrelevant, failures more often involve over-crediting reassurance. The model treats something with the surface form of responsibility — review, oversight, reputation, comfort, usability, or user approval — as if it resolves the original moral concern.

For the full write-up, see:

- `docs/reports/preliminary_findings_evidence_strength_v1.md`

## Dataset policy

For now, keep the canonical evidence-strength dataset and the trap expansion dataset separate.

- `v3_evidence_strength_v1` should remain the frozen broad calibration dataset.
- `v3_evidence_strength_trap_expansion_v1` should remain a diagnostic stress test for weak safeguards and irrelevant reassurance.

A future frozen canonical v2 may merge selected trap-expansion items, but only after defining selection rules in advance. This avoids overfitting the benchmark to observed model failures while preserving the diagnostic value of the trap items.

## Summarising manual scores

Generate a Markdown score summary from a manual scoring CSV:

```bash
python src/moral_sycophancy_eval/summarise_manual_scores.py docs/failure_audits/v3_behaviour_evidence_strength_v1_manual_scores.csv --md docs/failure_audits/v3_behaviour_evidence_strength_v1_score_summary.md
```

For the trap expansion:

```bash
python src/moral_sycophancy_eval/summarise_manual_scores.py docs/failure_audits/v3_behaviour_evidence_strength_trap_expansion_v1_manual_scores.csv --md docs/failure_audits/v3_behaviour_evidence_strength_trap_expansion_v1_score_summary.md
```

## Historical notes

Earlier datasets remain in the repository as development history:

- `recognition_seed_v0`: smoke-test recognition baseline.
- `moral_reasoning_integrity_seed_v1`: pressure/evidence contrast.
- `moral_reasoning_integrity_seed_v2`: adds neutral and irrelevant-detail variants.
- `moral_reasoning_integrity_seed_v3`: harder, adversarially edited MCQ answer choices.
- `v3.2`: hard-evidence diagnostic set for partial moral updating.

The current behavioural evidence-strength work supersedes the older TODOs about moving from MCQ into behavioural free-response evaluation.
