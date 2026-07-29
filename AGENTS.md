# Justifiable Moral Corrigibility Under Pressure

This repository contains diagnostic evaluations testing whether language models preserve moral and safety reasoning integrity under pressure.

## Core construct

- Justified corrigibility: updating when correction is justified by relevant evidence or good reasons.
- Justified resistance: declining to update when the supplied evidence or reasons do not justify an update.
- Sycophancy: updating because of unjustified pressure, reassurance, authority, urgency, reputation, consensus or user preference.
- Rigidity: making no meaningful update despite evidence or reasons that justify one.
- Miscalibrated corrigibility: updating in the correct direction but by the wrong amount.

## Current research status

Keep these conceptually separate:

- Phase 1: recognition, integrity MCQ and initial behavioural evaluations.
- Phase 2: contemporary-model evidence-strength and trap-expansion results.
- Phase 3: catastrophic-risk governance and over-approval diagnostics.
- Action-logprob work: a separate behavioural/logit experiment measuring shifts between bounded and broader actions.

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


## Important paths

- `src/moral_eval/`
  Reusable evaluation, scoring, validation and dataset-generation library code.

- `scripts/`
  Command-line entry points and operational scripts.

- `scripts/log_prob/`
  Action-log-probability experiment runners and analysis scripts.

- `data/datasets/`
  Curated, versioned evaluation datasets intended to be retained.

- `data/generation_cells/`
  Dataset-generation specifications and crossed design cells.

- `data/seed_scenarios/`
  Manually written source scenarios and seed material.

- `data/generated/`
  Generated candidates, adjudication artefacts and review intermediates.
  New contents should normally remain ignored rather than committed.

- `experiments/`
  Reproducible experiment and pipeline manifests.

- `docs/`
  Design documents, dataset specifications, rubrics, reports and releases.

- `tmp/`
  Local generated outputs, logs, analyses and smoke-test results. Keep ignored.

- `tests/`
  Automated tests for library and script behaviour.

- `sql/`
  PostgreSQL schema, promotion and reporting SQL.