# Justifiable Moral Corrigibility Under Pressure

This repository contains diagnostic evaluations testing whether language models preserve moral and safety reasoning integrity under pressure.

## Core construct

- Justified corrigibility: updating when correction is justified by relevant evidence or good reasons.
- Justified rigidity: staying rigid when correction is unjustified by irrelevant evidence or bad reasons.
- Sycophancy: updating because of unjustified pressure, reassurance, authority, urgency, reputation, consensus or user preference.
- Miscalibrated rigidity: failing to update despite genuinely relevant evidence.
- Miscalibrated corrigibility: updating in the right direction but by the wrong amount.

## Current research status

Keep these conceptually separate:

- Phase 1: recognition, integrity MCQ and initial behavioural evaluations.
- Phase 2: contemporary-model evidence-strength and trap-expansion results.
- Phase 3: catastrophic-risk governance and over-approval diagnostics.
- Action-logprob work: a separate behavioural/logit experiment measuring
  shifts between bounded and broader actions.

Do not generalise small diagnostic results into broad benchmark or model-safety claims.

## Immediate priority through 13 August 2026

The guaranteed deliverable is the action-logprob experiment.

Complete the matched unresolved-neutral, unresolved-directive and resolved-neutral conditions, including counterbalanced action labels, before starting any optional linear-probe implementation.

## Repository conventions

- Curated datasets belong under `data/datasets/`.
- Generation specifications belong under `data/generation_cells/`.
- Generated candidate and review artefacts belong under `data/generated/`.
- Temporary model outputs belong under `tmp/` and should remain ignored.
- Action-logprob scripts belong under `scripts/log_prob/`.
- Use the repository `.venv` explicitly for commands.
- Use UK English in documentation and reports.

## Constraints

- Prefer the smallest implementation that tests the stated hypothesis.
- Do not add database tables, generic experiment frameworks or mechanistic-interpretability packages unless explicitly requested.
- Do not expand datasets merely to increase sample count.
- Distinguish raw logits, restricted probabilities over nominated labels, ordinary generated responses and independently justified action boundaries.
- Never describe restricted two-label probabilities as unconditional action probabilities.
- Track model identity, model revision, prompt version, dataset version, device, dtype and software versions.