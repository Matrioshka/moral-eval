# Phase 3 dataset generation module

This module adapts useful ARENA dataset-generation techniques to free-response, multi-stage scenarios for **Justifiable Moral Corrigibility Under Pressure**. It does not create MCQs.

Phase 3 candidate generation is hypothesis-driven. The flagship construct is miscalibrated corrigibility: updating in the right direction by the wrong amount. The principal residual failure mode is over-approval after strong-but-incomplete safeguards.

Generated candidates are not a final dataset. They require deterministic validation and manual audit before pilot inclusion. Keep Phase 1 recognition, Phase 2 behavioural evaluation, and Phase 3 multi-stage pressure work conceptually separate.

For the full stage order, artefact names, package layout, and human review gates, see `docs/dataset_generation/lifecycle.md`.

## Generate candidates

Inspect a small set of cells before making any model calls:

```bash
python scripts/generate_phase3_dataset_candidates.py --list-cells --limit-cells 10
```

Use a targeted cell file and a small pass:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --provider openai-parse \
  --generator-model gpt-4o-2024-08-06 \
  --judge-model gpt-4o-2024-08-06 \
  --cells-json data/generation_cells/phase3_core_overapproval_cells.jsonl \
  --limit-cells 4 \
  --n-per-cell 1 \
  --max-workers 2 \
  --out data/generated/phase3_core_overapproval_v1
```

The run writes raw, scored, filtered, and deduplicated candidate JSONL, plus run summaries and `manual_review_template.csv`. The generated `kept_candidates.inspect.jsonl` is a convenience preview, not a curated pilot dataset.

## Debate/adjudication quality gate

Adjudication is a local pre-review gate. It records criterion-level scores, rationales, required edits, optional debate turns, and an explicit `keep`, `revise`, or `reject` verdict without granting final pilot approval.

Export one CSV row per candidate:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --export-adjudication-template \
  --out data/generated/phase3_core_overapproval_v1
```

The default input is `<out>/kept_candidates.jsonl` and the output is `<out>/adjudication_template.csv`. Use `--adjudication-input-jsonl` to adjudicate another filtered, kept, or hand-curated candidate JSONL, and `--adjudication-template-csv` to override the template path.

Complete every criterion column and the explicit `overall_verdict`, then apply the file locally:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --apply-adjudication \
  --adjudication-csv data/generated/phase3_core_overapproval_v1/adjudication_template.csv \
  --out data/generated/phase3_core_overapproval_v1
```

This writes `adjudicated_candidates.jsonl` and `adjudication_summary.json`. `keep` and `revise` candidates are retained; only `keep` is marked `pilot_ready_for_manual_review=true`; `reject` is excluded. Adjudication metadata never creates or changes `manual_review`. A later human review remains the only stage allowed to set `manual_review.phase3_pilot_candidate=true` and produce `phase3_pilot_candidates.jsonl`.

### Revise adjudicated candidates locally

Extract candidates whose adjudication verdict is `revise` and create an editable worksheet:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --extract-revise-candidates \
  --out data/generated/phase3_core_overapproval_v1
```

This reads `adjudicated_candidates.jsonl`, checks it against `adjudication_completed.csv`, and writes `revise_candidates.jsonl` plus `revision_notes.csv`. Complete `revision_notes` and at least one `revised_*` field for every row. Pressure turns, when changed, must be a JSON list of complete `PressureTurn` objects.

Apply the completed worksheet without model calls:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --apply-candidate-revisions \
  --out data/generated/phase3_core_overapproval_v1
```

The resulting `revised_candidates.jsonl` preserves the original candidate and adjudication under `revision`, leaves `manual_review` unchanged, and clears the active adjudication so each revised candidate can be re-adjudicated.

## Quota mode under rate limits

Quota mode is bounded candidate triage, not permission to generate a large dataset. Keep the target tied to a pilot hypothesis, use explicit cells, and start with one worker:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --provider openai-parse \
  --cells-json data/generation_cells/phase3_core_overapproval_cells.jsonl \
  --quota-mode \
  --target-kept 6 \
  --max-batches 2 \
  --n-per-cell 1 \
  --max-workers 1 \
  --out data/generated/phase3_core_overapproval_v1
```

Increase concurrency only after observing provider limits. The client uses rate-limit-aware retry and jitter, but a low worker count remains the safest control for token-per-minute limits.

## Manual review

Copy `manual_review_template.csv` to `manual_review_completed.csv` and complete these columns for every retained `case_id`:

- `manual_decision`: `keep`, `revise`, or `reject`
- `manual_reason`
- `required_edits`
- `phase3_pilot_candidate`: explicit `true` or `false`

Audit construct validity, evidence strength, target update magnitude, access bounds, pressure isolation, residual blockers, and whether generic caution could pass. A `reject` row is always excluded. A `revise` row is excluded unless it is explicitly marked as a pilot candidate.

## Apply manual review

This mode is local and does not construct an API client:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --apply-manual-review \
  --out data/generated/phase3_core_overapproval_v1
```

Defaults:

- input: `<out>/kept_candidates.jsonl`
- review: `<out>/manual_review_completed.csv`
- output: `<out>/phase3_pilot_candidates.jsonl`

Use `--review-input-jsonl`, `--manual-review-csv`, and `--pilot-output-jsonl` to override them. Candidates with deterministic validation errors are excluded unless `--allow-reviewed-validation-errors` is supplied deliberately.

## Merge reviewed pilots

Merge several reviewed runs into one curated candidate file:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --merge-pilot-candidates \
  --pilot-inputs \
    data/generated/run_a/phase3_pilot_candidates.jsonl \
    data/generated/run_b/phase3_pilot_candidates.jsonl \
  --pilot-output-jsonl data/generated/phase3_pilot_v1/phase3_pilot_candidates.jsonl
```

Identical duplicate `case_id` records are retained once in first-seen order. Conflicting records with the same `case_id` fail clearly.

## Export to the current Inspect dataset shape

The reviewed export targets the existing multi-stage record contract consumed by `src/moral_eval/behaviour.py`. Behavioural prompts and pressure turns remain separate from judgement and review metadata.

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --export-inspect-jsonl \
  --pilot-input-jsonl data/generated/phase3_pilot_v1/phase3_pilot_candidates.jsonl \
  --inspect-output-jsonl data/datasets/phase3/phase3_core_overapproval_pilot_v1.jsonl \
  --dataset-version phase3_core_overapproval_pilot_v1
```

Only records with an attached non-reject manual review and `phase3_pilot_candidate=true` can be exported. Register the final dataset path/version in `behaviour.py` when promoting the pilot into a runnable task configuration.

## Warning

Do not treat model-generated or QC-filtered candidates as final evaluation data. LLM QC is triage, not construct validation. Every released pilot item needs a human audit, explicit provenance, deterministic validation, and a defensible scoring envelope.
