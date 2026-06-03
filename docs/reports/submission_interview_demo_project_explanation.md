# Submission / interview / demo project explanation

This note contains short reusable explanations of the project for submission, interview, and live-demo contexts. It is not the canonical technical report. The canonical project write-up remains `docs/reports/final_project_report_justifiable_moral_corrigibility_under_pressure.md`.

## Short spoken version

This project evaluates whether language models preserve moral and safety reasoning integrity when a user pressures them to relax a judgement.

The central distinction is between corrigibility and sycophancy. A good model should update when correction is justified, partially update when evidence is relevant but incomplete, and resist pressure when the pressure does not actually answer the original concern.

The project started with recognition and multiple-choice tests of moral sycophancy, then moved into behavioural free-response evaluation. The final phase focuses on high-stakes AI release-governance scenarios, where a model must choose between access levels such as internal red-teaming, external safety audit, external research, trusted partner evaluation, customer operational use, hosted fine-tuning, adapter release, or open-weight release.

The main failure mode found is not crude agreement with the user. Strong models often reject broad release or open weights. The subtler failure is over-expansion: moving from bounded safety evaluation to a more permissive external-access compromise when the user supplies partial safeguards, institutional pressure, customer value, research goodwill, or urgency.

The final Phase 3 diagnostic uses a schema-v2.1 structured access tuple: access intent, access population, access modality, real-world exposure, and externalisation level. This makes intermediate boundary errors more inspectable than a single release-scope label.

The project uses Inspect for eval execution. It keeps manual audit as the source of substantive moral-reasoning judgement, and adds a deterministic schema-v2.1 scorer that checks whether the model's selected access tuple is valid, internally consistent, and within the dataset's acceptable bounds.

## Submission-oriented version

My project is an Inspect-based evaluation suite for justifiable moral corrigibility under pressure.

The question is: when a user or operator pushes a model to relax a moral or safety judgement, does the model update in proportion to the evidence, or does it capitulate to authority, urgency, reassurance, institutional consensus, emotional pressure, or customer value?

The core distinction is:

```text
Corrigibility means willingness to be corrected for good reasons.
Sycophancy means willingness to be corrected for bad reasons.
Rigidity means unwillingness to be corrected even for good reasons.
Miscalibrated corrigibility means updating in the right direction, but by the wrong amount.
```

The final phase focuses on AI release governance, because this is a realistic high-stakes domain where the right answer is often neither "no access" nor "broad release". The model has to preserve distinctions between internal evaluation, external audit, non-operational research, trusted partner sandboxing, limited customer operational use, fine-tuning access, downloadable adapters, and open-weight release.

The main finding is that stronger models are often robust to weak or irrelevant reassurance, but they can still over-credit strong-but-incomplete safeguards. They may reject unrestricted release while still approving an intermediate access level that is too permissive for the evidence provided.

The final schema-v2.1 pilot has 11 release-governance cases. GPT-4.1-mini passed 6/11 and clearly failed 5/11. The failures concentrated in over-approval from bounded safety evaluation to broader external access under customer, research, scaffold, adapter, or incomplete-threat-model pressure.

Architecturally, the project now has two scoring layers. Manual audit remains the primary judgement for moral and safety reasoning quality. A deterministic Inspect scorer validates the structured access tuple against the dataset's minimum and maximum acceptable access bounds. This gives the project a narrow automated check without pretending that moral reasoning has been fully automated.

## Technical demo version

For the demo, explain the project as follows.

This is not a general morality benchmark. It is a targeted behavioural diagnostic for a specific safety-relevant failure mode: whether a language model preserves calibrated moral and safety reasoning when pressured to relax a boundary.

The final benchmark setting is AI release governance. Each scenario gives the model an earlier restrictive judgement and then supplies follow-up information from the user. The follow-up may include real safety evidence, but also pressure such as urgency, authority, research goodwill, institutional consensus, commercial value, or administrative reassurance.

The model then has to choose a structured access decision using schema-v2.1:

```text
access_intent
access_population
access_modality
real_world_exposure
externalisation_level
```

This schema matters because a single release label hides important distinctions. "Hosted access" could mean an external audit, non-operational research, trusted-partner sandboxing, limited customer use, or a staged product beta. Those are materially different risk boundaries.

The project's main diagnostic signal is over-expansion. The model often does not fully cave. It may reject open weights, unrestricted release, or downloadable artefacts. But it may still move too far: for example, from external safety audit to customer operational use, or from audit access to broader researcher access, when the supplied evidence does not justify that move.

The implementation uses Inspect. The original behavioural eval produces free-response outputs for manual audit. The newer schema-v2.1 scored variant appends an explicit JSON-output requirement and uses a deterministic scorer to extract the five-field tuple, validate enum values, check tuple consistency, and compare the selected access against the item's acceptable bounds.

That scorer is deliberately narrow. It is not presented as a replacement for manual moral-reasoning audit. It checks the access decision structure. Manual audit still assesses whether the model properly distinguishes pressure from evidence, preserves residual risks, and gives an adequate safety justification.

The current result is preliminary but coherent: the schema makes a subtle failure mode inspectable, and the deterministic scorer reproduces the same GPT-4.1-mini headline count as the manual audit on the frozen 11-item pilot.

## One-line version

This project tests whether models remain corrigible for good reasons while resisting pressure to over-relax high-stakes moral and safety boundaries, especially in AI release-governance access decisions.
