# Phase 3 dataset generation module

This module adapts the useful ARENA dataset-generation techniques to free-response scenarios for **Justifiable Moral Corrigibility Under Pressure**.

It intentionally does **not** create MCQs. It generates structured scenario candidates, scores them with a separate QC pass, filters weak items, performs lightweight near-duplicate detection, and exports candidate JSONL plus an Inspect-style free-response JSONL.

## Drop-in layout

Copy these files into the repo root:

```text
src/moral_sycophancy_eval/dataset_generation/
scripts/generate_phase3_dataset_candidates.py
tests/test_dataset_generation_module.py
requirements-dataset-generation.txt
```

## Recommended early workflow

Start small. Do not generate hundreds of items before validating the schema and rubric.

```bash
python scripts/generate_phase3_dataset_candidates.py --list-cells --limit-cells 10
```

Then run a small model-backed generation pass:

```bash
python scripts/generate_phase3_dataset_candidates.py \
  --provider openai-parse \
  --generator-model gpt-4o-2024-08-06 \
  --judge-model gpt-4o-2024-08-06 \
  --limit-cells 8 \
  --n-per-cell 1 \
  --max-workers 4 \
  --out data/generated/phase3_candidates_v1
```

Generated files:

```text
raw_candidates.jsonl
scored_candidates.jsonl
kept_candidates.jsonl
kept_candidates.inspect.jsonl
```

## What to manually inspect

Inspect `kept_candidates.jsonl` before turning anything into an eval dataset. Specifically check:

- Is evidence quality genuinely clear?
- Is the pressure type isolated?
- Is the target update magnitude defensible?
- Are residual blockers concrete?
- Would a model pass by generic caution rather than calibrated updating?
- Would a model pass by generic compliance rather than evidence recognition?

The aim is a crisp pilot, not a large slurry.
