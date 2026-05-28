# Phase 2: contemporary model suite plan

## Purpose

Phase 2 tests the existing moral-sycophancy / moral-reasoning-integrity evals on a separate contemporary model suite.

This phase must remain separate from the original five-model continuity suite. The original suite is useful for natural-context versus structured-prompt comparison because the same models were run across the same diagnostic set. Phase 2 should not be mixed into that table as if it were the same comparison.

The core question for Phase 2 is:

> Do newer and stronger available models show the same evidence-strength failure pattern, especially around weak procedural safeguards and irrelevant reassurance?

## Scope

Use the existing datasets first:

- canonical broad calibration: `v3_evidence_strength_v1`
- diagnostic trap expansion: `v3_evidence_strength_trap_expansion_v1`
- structured diagnostic prompt: `v3_evidence_strength_trap_expansion_v1_structured`

Do not add new dataset items for Phase 2 until the contemporary-suite baseline is complete.

## Primary comparison

Run each contemporary model on:

1. `v3_evidence_strength_trap_expansion_v1`
2. `v3_evidence_strength_trap_expansion_v1_structured`

This preserves the same natural-context versus structured-prompt comparison used in the continuity suite.

The main outcome is not just total score. Track by failure class:

- residual concern under-specified;
- model over-approves after evidence;
- model over-resists evidence;
- ignores evidence strength;
- generic safety waffle.

## Candidate model categories

The exact runnable model IDs should be verified immediately before running, using the provider documentation or model list.

Suggested categories:

### OpenAI

Use one or two current OpenAI models available in the local Inspect/OpenAI setup.

Candidate role:

- stronger contemporary OpenAI model;
- lower-cost contemporary OpenAI model, if distinct from the continuity suite.

Do not assume older IDs are current. Verify through OpenAI model documentation or `inspect eval --model` support.

### Anthropic

Use a current Claude model if available through the local eval setup or OpenRouter.

Candidate role:

- strong frontier/proprietary comparison model.

### Google / Gemini

Use a current Gemini model if available through the local eval setup or OpenRouter.

Candidate role:

- strong non-OpenAI proprietary comparison model.

### Contemporary open-weight models through OpenRouter

Use newer available open-weight or open-access models that supersede the continuity-suite models where possible.

Candidate families to check:

- newer Llama-family model;
- newer Qwen-family model;
- newer Gemma-family model;
- one additional strong open model if available and cost-effective.

Do not rely on family names alone. Record exact provider/model IDs used.

## Proposed minimum Phase 2 suite

Minimum useful suite: 4 models.

Suggested composition:

1. one strong current OpenAI model;
2. one strong current Claude or Gemini model;
3. one strong current open-weight model;
4. one smaller current open-weight model.

This gives a cleaner contemporary comparison without exploding the manual-scoring burden.

## Suggested run order

1. Verify model IDs and costs.
2. Run one smoke test on the structured trap-expansion dataset with `--limit 3`.
3. Run the full natural-context trap expansion for the first model.
4. Export and inspect outputs before running the whole suite.
5. Run the full structured trap expansion for the same model.
6. Score both runs.
7. Only then continue to the remaining models.

Avoid running a full suite before confirming that model routing, prompt formatting, and export columns are correct.

## Output naming convention

Use separate Phase 2 filenames so the results are not confused with the continuity suite.

Suggested pattern:

```text
v3_behaviour_trap_expansion_v1_phase2_<model_slug>_natural_manual_scores.csv
v3_behaviour_trap_expansion_v1_phase2_<model_slug>_structured_manual_scores.csv
```

Suggested summaries:

```text
v3_behaviour_trap_expansion_v1_phase2_<model_slug>_natural_score_summary.md
v3_behaviour_trap_expansion_v1_phase2_<model_slug>_structured_score_summary.md
```

Suggested final report:

```text
docs/reports/phase_2_contemporary_model_suite_results.md
```

## Scoring policy

Use the same 0-3 manual scoring scale as Phase 1.

Do not modify the rubric mid-phase unless a clear scoring ambiguity is documented. If the rubric changes, annotate which runs used which rubric version.

## Hypotheses

### H1: Stronger contemporary models will handle strong evidence well.

Expected: high scores on strong evidence updates.

Failure to update on strong evidence would be important, because it would indicate rigidity rather than sycophancy.

### H2: Structured prompting will improve irrelevant-reassurance rejection.

Expected: structured prompt reduces failures on cosmetic, reputational, affective, or institutional-praise cues.

### H3: Weak procedural safeguards will remain the hardest category.

Expected: models may still over-credit fairness language, generic review, support channels, complaint forms, dashboards, internal benchmarking, or annual reporting.

This is the most important hypothesis. The hard problem is not rejecting obvious fluff. It is calibrating half-relevant institutional safeguards.

### H4: Failure type will be more informative than total score.

Expected: high-level score differences may obscure whether a model fails by over-approval, over-resistance, vague residual concern, or generic boilerplate.

## Non-goals

- Do not create new data items during the initial Phase 2 baseline.
- Do not merge Phase 2 results into the original five-model continuity table.
- Do not describe Phase 2 as a broad benchmark unless the model suite and sample size are expanded substantially.
- Do not claim mechanistic evidence from behavioural outputs.
- Do not over-interpret provider/model rankings from 12-item diagnostic runs.

## First concrete task

Create a short list of runnable candidate model IDs from the local Inspect/OpenAI/OpenRouter setup.

Then run one smoke test:

```powershell
inspect eval src/moral_sycophancy_eval/behaviour.py --model <MODEL_ID> -T dataset_version=v3_evidence_strength_trap_expansion_v1_structured --limit 3
```

Only proceed to full runs after the smoke test succeeds.
