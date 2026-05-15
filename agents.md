# Moral Sycophancy Eval

This repo builds an Inspect-based evaluation for moral sycophancy under user pressure.

## Core construct

Moral sycophancy under user pressure is a model’s tendency to change, soften, rationalise, or reverse a moral judgement primarily because of user pressure, rather than because the user supplied relevant evidence, corrected a factual error, or offered a stronger argument.

## Working principles

- Prioritise construct validity over dataset size.
- Distinguish sycophancy from legitimate updating.
- Use paired prompts wherever possible.
- Keep scoring simple before adding model-graded free-form scoring.
- Avoid overclaiming about deception, inner motives, or scheming.
- Track prompt versions, model versions, dataset versions, random seeds, and scoring rules.
- Use UK English in docs and reports.

## Initial implementation target

Build a minimal Inspect eval with:
- a recognition baseline: can the model identify which answer is more morally sycophantic?
- a behavioural MCQ eval: does the model choose the sycophantic answer under pressure?
- answer shuffling
- metadata fields for pair_id, moral_domain, pressure_type, and expected_failure_mode