# Action-logprob environment

Python 3.12 is the supported project environment for the action-logprob
experiment. Use the repository virtual environment explicitly.

## Create and populate the environment

From the repository root on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-action-logprob.txt
```

For Mistral tokenizer compatibility, additionally install:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-action-logprob-mistral.txt
```

Torch is deliberately absent from both action-logprob requirements files.
For laptop CPU development and model-free checks, install the CPU build from
the official PyTorch index:

```powershell
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

For a CUDA run, install the Torch build appropriate to the selected cloud or
desktop CUDA image. Do not copy the CPU command and do not guess a CUDA wheel
command before selecting that image.

## Verify the environment

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import numpy; print(numpy.__version__)"
.\.venv\Scripts\python.exe -c "import transformers; print(transformers.__version__)"
.\.venv\Scripts\python.exe -c "import torch; print(torch.__version__); print(torch.cuda.is_available()); print(torch.version.cuda)"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m unittest tests.test_action_logprob_mvp
```

The laptop CPU is for development, documentation checks and model-free tests;
do not use it to run the 24B Mistral model. Do not commit `pip freeze`
snapshots or other generated environment output from `tmp/`.

## Mistral replication smoke test

Use the same user messages as the frozen Qwen pilot. Mistral's native
instruction rendering is permitted, but do not add the repository's suggested
`SYSTEM_PROMPT.txt` or any other model-specific system content.

1. Install the Mistral-specific dependencies on the selected cloud image.
2. Resolve the model repository to an exact 40-character commit revision and
   record it before running anything.
3. Load the official `MistralTokenizer` only from that pinned revision.
4. Invoke `--tokenizer-only` separately for each frozen dataset. Each invocation
   must report the A/B token IDs and `12/12` successful rendered boundaries.
5. On the rented 80 GB GPU, load the model with CUDA BF16.
6. Run `--smoke-only` first on the frozen autonomous-cyber dataset and verify
   its next-token logits and deterministic ordinary generation.
7. Only then run the frozen 36-prompt replication.

Example tokenizer-only shape (replace the placeholder with the resolved commit
hash and repeat with each frozen dataset path):

```powershell
.\.venv\Scripts\python.exe .\scripts\log_prob\run_action_logprob_mvp.py --model mistralai/Mistral-Small-3.2-24B-Instruct-2506 --revision <40-character-commit> --data .\data\datasets\action_logprob\action_logprob_positive_control_gate_v0.jsonl --tokenizer-only
```

`MistralTokenizer.from_hf_hub` supports a `revision` argument in current
`mistral-common`. The runner checks that capability at runtime; if an installed
compatible release lacks it, the runner downloads `tekken.json` with the
revision-aware Hugging Face Hub API and loads it with
`MistralTokenizer.from_file`. A full Mistral run requires exact revision
provenance before model weights are loaded.

The preregistered first cloud smoke command is:

```powershell
.\.venv\Scripts\python.exe .\scripts\log_prob\run_action_logprob_mvp.py --model mistralai/Mistral-Small-3.2-24B-Instruct-2506 --revision 95a6d26c4bfb886c58daf9d3f7332c857cb27b43 --data .\data\datasets\action_logprob\action_logprob_positive_control_gate_v0.jsonl --device cuda --smoke-only --output .\tmp\action_logprob_mvp\mistral_small_3_2_24b_autonomous_cyber_smoke_v1.json
```

Choose a new `--output` path for every attempt. Smoke mode refuses to overwrite
an existing result. Successful JSON is marked `status: "passed"`; a post-score
validation failure is marked `status: "failed"` and includes `failure_stage`
and `failure_reason`.
