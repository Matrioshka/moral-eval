# Moral sycophancy recognition seed eval

This is a minimal Inspect baseline for recognising moral sycophancy under user pressure.

It contains 12 hand-written seed items across 6 moral domains, with 2 examples per domain. The sycophantic answer is balanced across answer positions: 6 `A`, 6 `B`.

## Files

- `data/moral_sycophancy_recognition_seed_v0.jsonl`: seed dataset
- `src/moral_sycophancy_eval/recognition.py`: Inspect task using `multiple_choice()` and `choice()`

## Run

From the project root:

```bash
inspect eval src/moral_sycophancy_eval/recognition.py --model openai/gpt-4o-mini --limit 5
```

Then run the full seed set:

```bash
inspect eval src/moral_sycophancy_eval/recognition.py --model openai/gpt-4o-mini
```

Use whichever model/provider string is configured in your Inspect environment.
