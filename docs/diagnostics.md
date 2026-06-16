# Passive Behavioural Diagnostics

Behavioural evaluations must remain clean. Diagnostics are collected after model
responses are produced and must not change prompt wording, message sequencing,
pressure-turn logic, scoring, datasets, or model-call behaviour for default runs.

## Usage Metadata

Usage metadata is passive and default-on. The pipeline records token fields when
Inspect or the provider payload makes them available:

- input tokens
- output tokens
- total tokens
- reasoning tokens, if available
- thinking tokens, if available
- cached input tokens, if available

Missing provider fields are stored as `NULL`. The pipeline does not infer missing
reasoning or thinking counts.

Usage-only diagnostics are headline-eligible because they do not alter the
behavioural dialogue.

## Provider Reasoning Summaries

Provider reasoning summaries are diagnostic-only and default-off. In v1 the
pipeline may only extract summaries already present in raw provider metadata.
Requesting provider reasoning summaries is rejected as not implemented in v1.

Provider reasoning summaries are not headline-eligible. They must not be used as
faithful chain-of-thought evidence, and they must not be mixed into headline
Phase 3 results.

## Dialogue Boundary

Diagnostics must never be fed back into later pressure rounds. The behavioural
dialogue must not include prompted rationales, extracted summaries, judge
outputs, rubric labels, diagnostic metadata, or other derived information.

Main Phase 3 claims should use clean behavioural runs with passive usage
metadata only.
