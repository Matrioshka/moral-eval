# Executive summary: Justifiable moral corrigibility under pressure

## What was the project?

This project developed an Inspect-based evaluation suite for testing whether language models preserve moral and safety reasoning integrity under user pressure.

The central construct is **justifiable moral corrigibility under pressure**:

```text
A good model should update when correction is justified,
partially update when evidence is relevant but incomplete,
and resist pressure when the pressure does not answer the original concern.
```

The project distinguishes:

```text
Corrigibility = willingness to be corrected for good reasons.
Sycophancy = willingness to be corrected for bad reasons.
Rigidity = unwillingness to be corrected even for good reasons.
Miscalibrated corrigibility = updating in the right direction but by the wrong amount.
```

## Why does it matter?

In high-stakes AI governance, failures are not always crude refusals or crude compliance. A more realistic failure mode is pressure to relax a safety boundary because the proposed compromise sounds responsible.

Examples include:

```text
A customer wants early API access.
A respected researcher wants broader access.
Leadership cites institutional consensus.
A team claims monitoring and revocation are enough.
An emergency creates pressure for immediate deployment.
```

The safety-relevant question is whether the model can distinguish genuine safety evidence from pressure, reassurance, urgency, authority, research goodwill, customer value, or administrative safeguards.

This matters for catastrophic-risk governance because access-boundary decisions around frontier AI systems can affect cyber, biosecurity, agentic scaffold, fine-tuning, adapter, and weight-release risks.

## What was built?

The project progressed through several stages:

```text
1. Recognition baseline
2. Integrity multiple-choice evaluation
3. Behavioural free-response evaluation
4. Evidence-strength datasets
5. Trap-expansion diagnostic dataset
6. Phase 3 release-governance access-boundary pilot
```

The final Phase 3 diagnostic is the frozen 11-item schema-v2.1 release-governance pilot:

```text
v4_justifiable_corrigibility_release_governance_schema_v2_1_11item_pilot
```

It asks models to make structured access decisions using this tuple:

```text
access_intent
access_population
access_modality
real_world_exposure
externalisation_level
```

This schema separates distinctions that a single release-scope ladder tended to blur, especially:

```text
bounded safety evaluation access != research access != partner evaluation != customer operational use
```

## What was found?

Phase 1 and Phase 2 showed that stronger contemporary models mostly reject weak or irrelevant reassurance in the current diagnostics. The residual failure mode shifted toward **over-approval after strong-but-incomplete safeguards**.

Phase 3 sharpened that finding in AI release-governance scenarios.

On the frozen 11-item schema-v2.1 pilot, GPT-4.1-mini produced:

```text
Pass: 6/11
Borderline: 0/11
Clear failure: 5/11
```

The failures clustered around over-expansion from bounded safety evaluation to more permissive external access:

```text
external safety audit -> customer operational use
external safety audit -> external non-operational research
external safety audit -> customer/partner operational use for an agentic scaffold
external safety audit -> trusted partner/staged access around a downloadable adapter
external safety audit -> customer operational use despite incomplete threat-model coverage
```

A small multi-model diagnostic comparison found:

| Endpoint / route | Pass | Borderline | Clear failure | Total |
|---|---:|---:|---:|---:|
| `openai/gpt-4.1-mini` | 6 | 0 | 5 | 11 |
| `openai/gpt-5.5` | 8 | 0 | 3 | 11 |
| `openrouter/anthropic/claude-opus-4.8` | 8 | 0 | 3 | 11 |
| `openrouter/google/gemini-3.5-flash` | 9 | 1 | 1 | 11 |

The main finding is not simple sycophancy. The tested models often reject broad release, open weights, fine-tuning, or unrestricted access. The more diagnostic failure is subtler: selecting a plausible intermediate access category that exceeds the justified evidence boundary.

## What are the limitations?

This is a small diagnostic pilot, not a benchmark leaderboard.

Important limitations:

| Limitation | Consequence |
|---|---|
| Small N | Results should not be interpreted statistically. |
| Manual scoring | Judgements are inspectable, but not yet independently replicated. |
| Structured prompt | Results may partly reflect schema-following ability rather than deeper safety judgement. |
| OpenRouter routing | Some results are endpoint/provider-route results, not pure model-family results. |
| Single-turn pressure | The eval does not yet test extended negotiation or repeated escalation. |
| Narrow domain | Release governance is important but not exhaustive of moral corrigibility. |

OpenRouter-routed results should be interpreted as behaviour of those routed endpoints under this eval harness, not as definitive claims about all Claude or Gemini deployments.

## What should be done next?

The immediate next step is report polish, not more experiments.

High-value future work:

```text
1. Add independent human scoring or inter-rater reliability checks.
2. Run native-provider replications for OpenRouter-routed endpoints.
3. Add repeated-run stability checks for surprising endpoint results.
4. Test multi-turn pressure escalation rather than single-turn follow-ups.
5. Extend schema-v2.1 to other catastrophic-risk domains only after defining new hypotheses in advance.
```

Do not add more Phase 3 release-governance items merely to increase sample count.

## Bottom line

This project reframes moral sycophancy as a problem of **calibrated updating under pressure**.

The strongest current result is that models may reject obviously dangerous release options while still over-expanding from bounded safety evaluation to broader external access. That is a realistic and safety-relevant failure mode for AI governance.

The contribution is not a large benchmark. It is a focused, inspectable diagnostic for justifiable moral corrigibility under pressure.
