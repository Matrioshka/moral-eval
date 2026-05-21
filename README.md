# Moral sycophancy recognition seed eval

Small Inspect eval suite for testing moral sycophancy and moral reasoning integrity under user pressure.

It contains  hand-written seed items across 6 moral domains, with 2 examples per domain. The sycophantic answer is balanced across answer positions: 6 `A`, 6 `B`.

## Files

- `data/moral_sycophancy_recognition_seed_v*.jsonl`: seed dataset
- `src/moral_sycophancy_eval/recognition.py`: Inspect task using `multiple_choice()` and `choice()`

## Run

From the project root:

```bash
inspect eval src/moral_sycophancy_eval/recognition.py --model openai/gpt-4o-mini --limit 3
```

Then run the full seed set:

```bash
inspect eval src/moral_sycophancy_eval/recognition.py --model openai/gpt-4o-mini
```

Use whichever model/provider string is configured in your Inspect environment.


## Moral reasoning integrity

Tests whether a model:

* resists irrelevant pressure
* updates on relevant evidence
* maintains judgement in neutral cases
* acknowledges irrelev*ant detail without reversing

```bash
inspect eval src/moral_sycophancy_eval/integrity.py --model openai/gpt-4o-mini -T dataset_version=v3
```

## Dataset validation
```bash
python src/moral_sycophancy_eval/validate_dataset.py data/moral_reasoning_integrity_seed_v3.jsonl --expected-variants pressure,evidence,irrelevant_detail,neutral
```

## Result summaries
```bash
python src/moral_sycophancy_eval/summarise_results.py
```

## Current status
- recognition_seed_v0: smoke-test recognition baseline.
- moral_reasoning_integrity_seed_v1: pressure/evidence contrast.
- moral_reasoning_integrity_seed_v2: adds neutral and irrelevant-detail variants.
- moral_reasoning_integrity_seed_v3: harder, adversarially edited answer choices.

Main known hard case:

mri-v3-007-evidence: workplace monitoring evidence case, where models may over-preserve the privacy objection instead of partially updating.

## Next TODOs

- Run `v3` on at least one weaker/open-weight model.
- Manually audit all `v3` items for answer-style leakage.
- Add `difficulty` and `difficulty_notes` fields to future datasets.
- Create `v4` only after the `v3` audit is complete.
- Consider model-graded free-response behavioural eval after MCQ baseline is stable.