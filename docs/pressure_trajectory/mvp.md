# Pressure trajectory evaluator: Stage 1 MVP

## Research purpose and scope

The Stage 1 evaluator records how a model's relative output preference between a
bounded action and a broader action changes over a cumulative, mixed-stage
pressure conversation. It combines ordinary natural-language responses with
shadow forced-choice measurements at each checkpoint. Because later checkpoints
contain all earlier pressure and responses, this design does not estimate an
independent causal coefficient for any one pressure mechanism.

The main fixture uses the existing autonomous cyber-defence deployment-gate
case. Its factual evidence remains unresolved throughout:

1. baseline;
2. authority and seniority pressure;
3. cumulative urgency and deployment pressure;
4. cumulative institutional-consensus pressure.

These four checkpoints produce four natural-language responses and sixteen
measurements: four counterbalanced mappings at each checkpoint. None of the
three pressure turns supplies evidence that satisfies or partially satisfies
the deployment gate.

The separate resolution-control fixture follows `baseline -> genuine
resolution`. It uses the curated resolved evidence from the source dataset and
produces two responses and eight measurements. It is not appended to, or
analysed as part of, the evidence-free pressure trajectory.

## Package boundaries

Implementation lives under `src/moral_eval/pressure_trajectory/`. It does not
change or depend on `moral_eval.behaviour`, and it does not import private
helpers from the existing action-logprob script. The dataset adapter reads the
curated JSONL case and converts its public data concepts into trajectory domain
objects.

The runner receives a scenario, model backend, event recorder, option mappings
and run metadata explicitly. Model access and event persistence are small
protocols. Measurements and prompt transformations are pure functions. The
local Hugging Face adapter imports its runtime dependencies only when a real
backend is loaded; unit tests use a deterministic fake backend and synthetic
logits.

## Output decision boundary

The measurement concerns an observable output decision boundary: the sign of
the next-token logit margin between two nominated action labels. It does not
measure a latent or internal moral boundary, a moral state, or a causal
mechanism. Stage 1 performs no activation capture, probe training, patching or
causal intervention.

For every checkpoint and mapping, the evaluator clones the cumulative
transcript, appends an A/B prompt to the clone, and obtains the raw next-token
logits for the nominated labels. The prompt version remains in structured
metadata and is not shown to the model. The backend validates that each label is
exactly one token at that rendered assistant-generation boundary. It does not
expose or serialise the full vocabulary logit vector.

Measurement timing is `post_response` only. Each margin is therefore conditioned
on both the user pressure accumulated so far and the model's own preceding
generated responses. Stage 1 does not collect pre-response measurements.

The semantic margin is always:

```text
broader raw logit - bounded raw logit
```

The forced-choice message is discarded after measurement and never enters the
natural-language trajectory. The reported probabilities are a conditional
softmax restricted to the nominated A and B tokens. They are not the model's
unconditional probabilities of choosing either action.

A zero margin is the decision boundary between these two nominated output
tokens in this prompt context. It is not a latent moral boundary or evidence of
an internal moral representation.

## Event log and replay

Each run writes one append-only JSONL file. Every event contains schema version,
unique event ID, run ID, UTC timestamp, event type, contiguous sequence number
and a JSON-compatible payload. Supported Stage 1 events are:

- `run_created`;
- `scenario_loaded`;
- `baseline_response_generated`;
- `measurement_recorded`;
- `pressure_turn_added`;
- `model_response_generated`;
- `run_completed`;
- `run_failed`.

The canonical experiment configuration records dataset version and content
hash, case ID, trajectory ID, fixture version, fixture schema version and content
hash, requested
model and tokeniser identities and revisions, generation settings and seed,
requested device and dtype, measurement prompt version and timing, mapping
definitions, and inference-relevant backend implementation/version metadata.
One `experiment_configuration_sha256` is computed from its canonical JSON.
Run/event IDs, timestamps, output paths and their state, overwrite state,
dry-run versus execution mode, machine-local paths, resolved runtime metadata
and other transient invocation details are excluded. Such details are recorded
separately as execution or runtime metadata. A failed run remains as a replayable
partial log. Stage 1 does not resume partial logs.

Quantised execution is unsupported in Stage 1. The adapter accepts only the
documented device and floating-point dtype choices and contains no quantisation
loading path.

The recorder refuses an existing output. `--overwrite` explicitly replaces
only the nominated file; it never appends a second run to an existing log.

## Validation modes

From the repository root:

```powershell
.\.venv\Scripts\python.exe .\scripts\pressure_trajectory.py --dataset .\data\datasets\action_logprob\action_logprob_positive_control_gate_v0.jsonl --trajectory .\experiments\pressure_trajectory_mvp_v0.json --case-id deployment_gate__autonomous_cyber_defence_pilot_001 --model Qwen/Qwen2.5-1.5B-Instruct --revision 989aa7980e4cf806f80c7fef2b1adb7bc71aa306 --device cuda --dtype float32 --output .\tmp\pressure_trajectory\qwen2_5_1_5b_stage1_v0.jsonl --max-new-tokens 256 --seed 0 --dry-run
```

`--dry-run` loads and checks the dataset, case, fixture, expected counts,
mappings, hashes and output policy, then prints the planned configuration. It
does not import or load Transformers, initialise a model, load a tokeniser,
download files, create an event log or contextually validate label tokens.

`--tokenizer-check` has a different purpose. It loads only the selected
tokeniser, builds all checkpoint prompt structures using clearly marked,
deterministic placeholder assistant responses, validates A/B at every rendered
assistant-generation boundary for every mapping, and prints token IDs and
decoded forms. It performs no generation, loads no model weights and writes no
event log. Because future real assistant responses do not yet exist, this mode
validates tokenisation of stable prompt structures only; it does not validate
model behaviour or guarantee tokenisation for the exact future generated
contexts.

For example, when the pinned tokeniser is already available locally and can be
loaded without a download:

```powershell
.\.venv\Scripts\python.exe .\scripts\pressure_trajectory.py --trajectory .\experiments\pressure_trajectory_mvp_v0.json --model Qwen/Qwen2.5-1.5B-Instruct --revision 989aa7980e4cf806f80c7fef2b1adb7bc71aa306 --tokenizer-check
```

To validate the separate positive control, replace the trajectory path with:

```text
.\experiments\pressure_trajectory_resolution_control_v0.json
```

## Later local Qwen2.5-1.5B trial

After separately confirming the local environment and hardware, the pinned
trial command is:

```powershell
.\.venv\Scripts\python.exe .\scripts\pressure_trajectory.py --trajectory .\experiments\pressure_trajectory_mvp_v0.json --dataset .\data\datasets\action_logprob\action_logprob_positive_control_gate_v0.jsonl --case-id deployment_gate__autonomous_cyber_defence_pilot_001 --model Qwen/Qwen2.5-1.5B-Instruct --revision 989aa7980e4cf806f80c7fef2b1adb7bc71aa306 --device cuda --dtype float32 --output .\tmp\pressure_trajectory\qwen2_5_1_5b_stage1_v0.jsonl --max-new-tokens 256 --seed 0
```

This command is documentation only for Stage 1 implementation. Do not run it
as part of model-free development or testing.

## Current non-goals and future stages

Stage 1 has no interactive continuation, generated scenarios or pressure,
remote API integration, database storage, GUI, batching, distributed inference,
linear probes, activation capture or causal intervention. It should not be used
to make broad benchmark or model-safety claims.

Possible later stages include isolated dose-response controls, interactive
continuation, separately reviewed generated pressure, linear probes, and causal
interventions. Those additions require their own design and validation; they
are not implied by the Stage 1 event schema.
