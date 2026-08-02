# Action-logprob three-model comparison v0

## Scope

This report compares directional patterns from the frozen action-logprob
diagnostic across Qwen2.5-1.5B, Mistral Small 3.2 24B and Qwen2.5-14B. It uses
three curated explicit-gate cases, three matched conditions and four
counterbalanced mappings per case.

The setting is forced-choice and single-token: A or B. It is not a benchmark,
does not support population inference or a broad model-safety ranking, and is
separate from the project's Phase 1, Phase 2 and Phase 3 evaluations.

## Sources and validation

The deterministic model-free analyser read exactly these nine canonical
results:

| Model | Case | Raw JSON SHA-256 |
| --- | --- | --- |
| Qwen2.5-1.5B | Autonomous cyber | `0c0c73302bdad795969c5dcb441878bb09204bc16fe45526557464feaeb3ff6a` |
| Qwen2.5-1.5B | AI-assisted biosecurity | `4a3ca292bca7cad0a42f4dee824c0151d884e9899f20a642d0507c1c26183e8e` |
| Qwen2.5-1.5B | Critical infrastructure | `c8814cf71d05e26dbbfb364c334b4312684c358f7fb1644eb1a42e2a106a8cc9` |
| Mistral Small 3.2 24B | Autonomous cyber | `5c56fb9631199bbaaf633865ca2524f2776fba2253575dd414c2645f7debb266` |
| Mistral Small 3.2 24B | AI-assisted biosecurity | `14e48b998c7bbc24b9e4e62b3c3a04ce64eced545f38580a9ed2e353d60fb920` |
| Mistral Small 3.2 24B | Critical infrastructure | `f7acf34dd0a8115cbfd6c06e1ea861939a602eccc6aa9047e02d62552868274a` |
| Qwen2.5-14B | Autonomous cyber | `947581cc87f98b0a72b80acc6c1e92c4746f1e1e407144a6864b3e603080f91c` |
| Qwen2.5-14B | AI-assisted biosecurity | `55ad18d4f1e29a0d4fa7bd9a1db98b00ceae42ea02a335c956563cd04233984e` |
| Qwen2.5-14B | Critical infrastructure | `dd596e887c3ff4d7620d9713b817fcb9e642ec16a779a24790e09bc9b51d14c4` |

All nine files passed schema, identity, revision, dataset, case, prompt,
mapping, condition, runtime and finite-value checks. Their frozen logical
prompt text and corresponding pre-template hashes match across models for
each case/mapping/condition. Model-specific native chat-template rendering and
tokenised representations were not compared.

The Mistral 9-entry artefact manifest and Qwen2.5-14B 12-entry final manifest
both passed. The Qwen2.5-14B bundle hash matched
`79db83b8c1bab6f1ed9e852b5d638fef2bca64b3d65945f67ee03fa9f2e28039`.
All nine raw-result hashes were identical before and after analysis.

Qwen2.5-1.5B's historical result files have exact requested and resolved model
revisions but unavailable (`null`) resolved-tokeniser revisions. Mistral and
Qwen2.5-14B record exact matching requested, model and tokeniser revisions.

## Measures

- semantic margin = broader-action raw logit minus bounded-action raw logit;
- directive effect = unresolved-directive margin minus unresolved-neutral
  margin;
- resolution effect = resolved-neutral margin minus unresolved-neutral margin;
- pressure-to-resolution ratio = mapping-specific directive effect divided by
  its matching resolution effect.

All values were recomputed from raw logits. Stored margins and effects were
consistency checks only. Ratio summaries are arithmetic summaries of the 12
mapping-specific ratios; no ratio of aggregate mean effects is calculated.

## Directional results

| Model | Positive directive effects | Positive resolution effects | Conforming generations |
| --- | ---: | ---: | ---: |
| Qwen2.5-1.5B | 12/12 | 12/12 | 36/36 |
| Mistral Small 3.2 24B | 12/12 | 12/12 | 36/36 |
| Qwen2.5-14B | 12/12 | 12/12 | 36/36 |

Both shifts were positive under every mapping in every case. This is robust
directional reproduction within the frozen design. It does not make the raw
effect magnitudes directly comparable across model sizes or FP32/BF16
precision.

Per-model numerical summaries, kept separate because raw effect scales differ:

- **Qwen2.5-1.5B FP32:** mean directive effect `3.762214`; mean resolution
  effect `3.233336`.
- **Mistral Small 3.2 24B BF16:** mean directive effect `5.5`; mean resolution
  effect `15.354167`.
- **Qwen2.5-14B FP32:** mean directive effect `16.047057`; mean resolution
  effect `54.693884`.

These values document each run; they are not placed on a shared interpretive
scale.

## Dimensionless pressure-to-resolution ratios

| Model | Mean | Median | Range | Defined | Undefined |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen2.5-1.5B | 1.443521 | 1.422742 | 0.518097–2.554191 | 12 | 0 |
| Mistral Small 3.2 24B | 0.357925 | 0.361407 | 0.313043–0.396552 | 12 | 0 |
| Qwen2.5-14B | 0.298968 | 0.315695 | 0.137493–0.473207 | 12 | 0 |

For Mistral and Qwen2.5-14B, the mapping-specific genuine-resolution effect
was larger than the directive effect in 12/12 mappings. Qwen2.5-1.5B was more
variable: its ratio crossed 1 across mappings and cases. Ratios are
dimensionless descriptive diagnostics; they do not remove all scale,
calibration or architecture differences.

![Mapping-specific pressure-to-resolution ratios](../figures/action_logprob_cross_model_pressure_resolution_ratio_v0.png)

## Condition signs and boundary crossings

| Model | Condition | Negative | Zero | Positive |
| --- | --- | ---: | ---: | ---: |
| Qwen2.5-1.5B | Unresolved neutral | 6 | 0 | 6 |
| Qwen2.5-1.5B | Unresolved directive | 3 | 0 | 9 |
| Qwen2.5-1.5B | Resolved neutral | 0 | 0 | 12 |
| Mistral Small 3.2 24B | Unresolved neutral | 12 | 0 | 0 |
| Mistral Small 3.2 24B | Unresolved directive | 11 | 1 | 0 |
| Mistral Small 3.2 24B | Resolved neutral | 0 | 0 | 12 |
| Qwen2.5-14B | Unresolved neutral | 12 | 0 | 0 |
| Qwen2.5-14B | Unresolved directive | 12 | 0 | 0 |
| Qwen2.5-14B | Resolved neutral | 0 | 0 | 12 |

Resolution crossed from negative to positive in 6/12 Qwen2.5-1.5B mappings
and remained positive in the other six. It crossed negative to positive in
12/12 Mistral and 12/12 Qwen2.5-14B mappings.

Directive pressure crossed negative to positive in 3/12 Qwen2.5-1.5B
mappings, remained negative in three, and remained positive in six. Mistral
had 11 negative-to-negative transitions and one negative-to-zero transition.
Qwen2.5-14B had 12 negative-to-negative directive transitions.

![Condition-level semantic-margin signs](../figures/action_logprob_cross_model_condition_sign_counts_v0.png)

## Mapping robustness

The diagnostic compared exact label swaps at fixed literal presentation order
and exact position swaps at fixed semantic label assignment. It covered all
three condition margins and both mapping-specific effects.

| Model | Dimension | Sign agreement | Mean symmetric relative difference | Median |
| --- | --- | ---: | ---: | ---: |
| Qwen2.5-1.5B | Label assignment | 21/30 | 1.083762 | 0.967551 |
| Qwen2.5-1.5B | Presentation position | 27/30 | 0.622678 | 0.421034 |
| Mistral Small 3.2 24B | Label assignment | 29/30 | 0.192007 | 0.090688 |
| Mistral Small 3.2 24B | Presentation position | 29/30 | 0.162779 | 0.080721 |
| Qwen2.5-14B | Label assignment | 30/30 | 0.268489 | 0.108954 |
| Qwen2.5-14B | Presentation position | 30/30 | 0.288252 | 0.175987 |

The symmetric relative difference is
`2 * abs(x - y) / (abs(x) + abs(y))`. A zero/zero comparison would be
reported as undefined with an explicit `both_zero` status; no such undefined
comparison occurred in these results.

Counterbalancing reveals material nuisance sensitivity, particularly for the
small Qwen model, but does not eliminate it. Sign agreement is therefore not
treated as magnitude invariance.

![Mapping robustness diagnostics](../figures/action_logprob_cross_model_mapping_robustness_v0.png)

## Interpretation and limitations

Across all three models, unsupported directive pressure moved the semantic
margin towards the broader action under every mapping even though unresolved
safety evidence was unchanged. Genuine resolution also moved the margin in
that direction under every mapping. Mistral and Qwen2.5-14B preserved a
negative broader-action margin under directive pressure while crossing to a
positive margin after genuine resolution; the smaller Qwen model had more
variable absolute signs and nuisance sensitivity.

This pattern is a candidate pressure-sensitive behavioural signal in the
tested setting. It does not establish intentional capitulation, awareness of
wrongdoing, an internal sycophancy mechanism, statistical significance,
population generalisation or broad model-safety ordering. Ordinary generation
only checks adherence to the nominated A/B format. Mapping counterbalancing
diagnoses nuisance sensitivity but cannot remove it.
