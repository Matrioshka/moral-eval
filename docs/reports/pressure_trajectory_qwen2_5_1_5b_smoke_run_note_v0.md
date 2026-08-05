# Qwen2.5-1.5B pressure-trajectory engineering smoke runs

The Stage 1 pipeline executed successfully end to end for both the main
pressure trajectory and the separate resolution control. Both runs used the
original `pressure_trajectory_mvp_v0.json` and
`pressure_trajectory_resolution_control_v0.json` fixtures. These immutable
logs are retained only as historical engineering smoke-test artefacts under
ignored `tmp/` storage:

- resolution control SHA-256:
  `3CB51F5C5DC5AFFE329D41986518323715C5C497BC3271B10DA44F26C0FC3041`;
- main pressure trajectory SHA-256:
  `046E811D70F5B5A8FD3D3E52B29A59479F386F2BEE4FEAEBB0B0152D80176F5A`.

The resolution positive control failed: its descriptive post-response balanced
mean moved from `+1.84375` unresolved to `+0.1875` resolved, a
resolved-minus-unresolved difference of `-1.65625`. The natural-language
recommendations were also internally inverted relative to their explanations,
naming one action while explaining why the other was justified. Responses
reached or appeared to reach the generation limit, but the v1 event schema did
not record completion metadata.

All measurements in these logs occurred after the corresponding prose response;
there are no pre-response values and no measurable self-anchoring shifts. The
four mappings are counterbalancing design cells, not four independent samples.
Accordingly, the descriptive movement across the main pressure trajectory is
not a substantive result and cannot be interpreted as evidence of pressure
susceptibility. The failed control motivated measurement version 2, whose
primary measurement occurs before the current prose response and whose
secondary post-response measurement can diagnose self-anchoring effects.

The raw event logs are not committed. Their SHA-256 values above identify the
ignored files exactly. No broad conclusion about Qwen2.5-1.5B, the Qwen2.5
family, or contemporary models follows from these engineering smoke tests.
