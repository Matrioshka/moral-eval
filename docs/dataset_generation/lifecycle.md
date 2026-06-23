# Dataset generation lifecycle

This document is the operational map for producing dataset candidates and promoting a small reviewed pilot into the JSONL shape used by the evaluation code.

The importable package is `src/moral_eval/`. Dataset-generation library code lives under `src/moral_eval/dataset_generation/`.

The authoritative resumable workflow is:

```powershell
.\.venv\Scripts\python.exe .\scripts\moral_gen.py
```

`moral_gen.py` uses PostgreSQL as a control and provenance layer. Candidate payloads remain files; the database records the manifest snapshot, stage state, human gates, artefact paths, hashes, row counts, and drift. It does not store API credentials or replace the payload files.

The older `scripts/generate_phase3_dataset_candidates.py` command remains available for direct library operations, but it is not the DB-controlled workflow described below.

## DB-controlled stage sequence

```text
generate_candidates
prepare_adjudication
apply_adjudication
prepare_revision
apply_revision
prepare_revised_adjudication
apply_revised_adjudication
prepare_manual_review
apply_manual_review
export_inspect_jsonl
```

Each invocation of:

```powershell
.\.venv\Scripts\python.exe .\scripts\moral_gen.py next <run-slug>
```

advances at most one stage. Run `next` again only after inspecting its output and, where required, completing the named human CSV.

Human gates are review tasks, not global run blockers. Opening a gate does not set the run to `waiting_human`. A consuming stage waits only when its own required completed CSV is absent.

| Stage | Inputs | Outputs / action |
| --- | --- | --- |
| `generate_candidates` | manifest and generation cells | Raw, scored, filtered, kept, diagnostic, and preview artefacts. This is the only model-calling stage and requires `safety.allow_model_calls: true`. |
| `prepare_adjudication` | `kept_candidates.jsonl` | `adjudication_template.csv`; opens the `adjudication` gate. |
| `apply_adjudication` | `adjudication_completed.csv` | `adjudicated_candidates.jsonl`, `adjudication_summary.json`; satisfies the adjudication gate. |
| `prepare_revision` | original adjudication outputs | `revise_candidates.jsonl`, `revision_notes.csv`; opens the `revision` gate when revise candidates exist. |
| `apply_revision` | `revision_notes_completed.csv` | `revised_candidates.jsonl`; satisfies the revision gate. |
| `prepare_revised_adjudication` | `revised_candidates.jsonl` | `revised_adjudication_template.csv`; opens the distinct `revised_adjudication` gate. |
| `apply_revised_adjudication` | `revised_adjudication_completed.csv` | `adjudicated_revised_candidates.jsonl`, `revised_adjudication_summary.json`; satisfies the revised-adjudication gate. |
| `prepare_manual_review` | original and revised adjudication results | `ready_for_manual_review.jsonl`, `manual_review_gate_template.csv`, `unresolved_for_manual_review.jsonl`, `manual_review_preparation_summary.json`; opens the `manual_review` gate when candidates are ready. |
| `apply_manual_review` | `manual_review_completed.csv` | `reviewed_candidates.jsonl`, `manual_review_summary.json`; satisfies the manual-review gate. |
| `export_inspect_jsonl` | **`reviewed_candidates.jsonl` only** | Manifest-configured final JSONL plus `inspect_export_summary.json`. It does not invoke Inspect. |

## Human-edited CSVs

The runner prints the exact template, completed-file path, and resume command whenever it opens a gate.

| Gate | Template | Human-completed file |
| --- | --- | --- |
| Original adjudication | `adjudication_template.csv` | `adjudication_completed.csv` |
| Revision | `revision_notes.csv` | `revision_notes_completed.csv` |
| Revised adjudication | `revised_adjudication_template.csv` | `revised_adjudication_completed.csv` |
| Final manual review | `manual_review_gate_template.csv` | `manual_review_completed.csv` |

Revised candidates must be adjudicated again. The original adjudication remains revision provenance but is not the active decision for the changed candidate.

The workflow permits one bounded revision cycle:

- second-pass `keep` proceeds to manual review;
- second-pass `reject` is excluded;
- second-pass `revise` is written to `unresolved_for_manual_review.jsonl` for escalation and does not enter another automatic revision loop.

Final export consumes `reviewed_candidates.jsonl` only. It must not export directly from adjudicated, revised, ready-for-review, or unresolved artefacts.

Dataset registration remains a separate explicit operation. Exporting JSONL does not modify `src/moral_eval/behaviour.py`, register a dataset version, invoke Inspect, or ingest logs.

For concise commands, see [operator_guide.md](operator_guide.md). A safe-by-default manifest is provided at [example_manifest.yaml](example_manifest.yaml).

## Boundary between generated data and released data

Generated or QC-filtered candidates are not final evaluation data. A candidate only becomes part of a pilot dataset after explicit human/manual review marks it as a pilot candidate.

The intended promotion chain is:

```text
generated candidate
→ deterministic validation
→ LLM QC triage
→ adjudication quality gate
→ optional revision loop
→ manual review
→ pilot candidate JSONL
→ Inspect/behaviour JSONL export
→ behaviour.py registration
→ smoke eval
→ Postgres ingest
```

Keep these stages conceptually separate. In particular, adjudication is a pre-review quality gate, not final approval.

## Source layout

```text
src/moral_eval/dataset_generation/
  schemas.py           # shared record schemas and metadata contracts
  generate_candidates.py
  prompts.py
  llm_clients.py
  validation.py
  qc_candidates.py
  dedupe_candidates.py
  adjudication.py      # pre-manual-review quality gate
  revision.py          # local human revision loop for revise cases
  manual_review.py     # final human pilot-selection gate
  export_jsonl.py      # merge/export to behaviour.py dataset shape
  pipeline.py          # generation/QC/filter orchestration
  run_summary.py
```

```text
scripts/
  generate_phase3_dataset_candidates.py   # CLI entry point for dataset-generation stages
  run_experiment_pipeline.py              # eval/run pipeline, separate from dataset generation
  backfill_inspect_logs_to_postgres.py    # Inspect log → Postgres ingest
```

The package namespace is `moral_eval`, so active imports should look like:

```python
from moral_eval.dataset_generation.adjudication import apply_adjudication
```

Do not reintroduce `moral_sycophancy_eval` as an active import namespace.

## Legacy direct-library workflow reference

The remainder of this document describes lower-level file commands retained for direct library use. New resumable runs should use `moral_gen.py` and the manifest-driven workflow above.

## Direct-library order

### 0. Set local environment

From the repository root:

```powershell
$env:PYTHONPATH = "$PWD\src"
```

Use the repository virtual environment explicitly when needed:

```powershell
.\.venv\Scripts\python.exe -m pytest .\tests\test_dataset_generation_module.py -q
```

### 1. Inspect target generation cells

This step is local and makes no model calls.

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --list-cells `
  --cells-json data/generation_cells/phase3_core_overapproval_cells.jsonl `
  --limit-cells 12
```

Purpose: check the domain × evidence-quality × pressure-type cells before spending API budget.

### 2. Generate, score, filter, and dedupe candidates

This is the main model-calling stage.

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --provider openai-parse `
  --generator-model gpt-4o-2024-08-06 `
  --judge-model gpt-4o-2024-08-06 `
  --cells-json data/generation_cells/phase3_core_overapproval_cells.jsonl `
  --limit-cells 8 `
  --n-per-cell 1 `
  --max-workers 2 `
  --out data/generated/phase3_core_overapproval_v2
```

Primary outputs:

```text
<out>/raw_candidates.jsonl
<out>/scored_candidates.jsonl
<out>/filtered_candidates.jsonl
<out>/kept_candidates.jsonl
<out>/kept_candidates.inspect.jsonl
<out>/manual_review_template.csv
<out>/run_summary.json
```

`kept_candidates.inspect.jsonl` is a preview/convenience artefact. It is not a reviewed pilot dataset.

### 3. Export adjudication template

This step is local and makes no model calls.

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --export-adjudication-template `
  --out data/generated/phase3_core_overapproval_v2
```

Default input:

```text
<out>/kept_candidates.jsonl
```

Default output:

```text
<out>/adjudication_template.csv
```

Use `--adjudication-input-jsonl` when adjudicating another candidate file, for example revised candidates.

### Human gate A: complete adjudication CSV

Complete every row in `adjudication_template.csv`.

Required decision fields:

```text
overall_verdict = keep | revise | reject
```

For each criterion, fill:

```text
<criterion>_score = 1..5
<criterion>_label = pass | revise | reject
<criterion>_rationale
<criterion>_required_edits = JSON list, usually []
```

Criteria:

```text
construct_targeting
evidence_quality_label
pressure_isolation
update_calibration
scope_envelope
ideal_answer_calibration
catastrophic_risk_relevance
scoring_tractability
wording_quality
```

Use a spreadsheet-style CSV editor if possible. JSON-in-CSV fields must still contain valid JSON, for example `[]` or `["Clarify missing threat-model coverage."]`.

When complete, copy or rename the working file if you want provenance:

```powershell
Copy-Item `
  .\data\generated\phase3_core_overapproval_v2\adjudication_template.csv `
  .\data\generated\phase3_core_overapproval_v2\adjudication_completed.csv
```

### 4. Apply adjudication

This step is local and makes no model calls.

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --apply-adjudication `
  --out data/generated/phase3_core_overapproval_v2 `
  --adjudication-csv data/generated/phase3_core_overapproval_v2/adjudication_completed.csv `
  --adjudicated-output-jsonl data/generated/phase3_core_overapproval_v2/adjudicated_candidates.jsonl
```

Outputs:

```text
<out>/adjudicated_candidates.jsonl
<out>/adjudication_summary.json
```

Behaviour:

```text
keep   → retained, pilot_ready_for_manual_review=true
revise → retained, pilot_ready_for_manual_review=false
reject → excluded from adjudicated_candidates.jsonl by default
```

Adjudication never sets or modifies `manual_review`. It cannot grant final pilot inclusion.

### 5. Optional revision loop for promising failures

Extract revise cases:

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --extract-revise-candidates `
  --out data/generated/phase3_core_overapproval_v2
```

Outputs:

```text
<out>/revise_candidates.jsonl
<out>/revision_notes.csv
```

### Human gate B: complete revision worksheet

For every row in `revision_notes.csv`, fill:

```text
revision_notes
```

and at least one actual revised candidate field:

```text
revised_title
revised_baseline_scenario
revised_initial_user_prompt
revised_pressure_turns_json
```

If pressure turns are changed, `revised_pressure_turns_json` must be a valid JSON list of complete pressure-turn objects. Do not use dummy edits to satisfy validation. If a case does not need a candidate-field change, it should not be in the revision pass.

Apply revisions:

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --apply-candidate-revisions `
  --out data/generated/phase3_core_overapproval_v2 `
  --revision-notes-csv data/generated/phase3_core_overapproval_v2/revision_notes.csv `
  --revised-output-jsonl data/generated/phase3_core_overapproval_v2/revised_candidates.jsonl
```

Output:

```text
<out>/revised_candidates.jsonl
```

The revised record preserves the original candidate and prior adjudication under revision provenance, clears active adjudication, and leaves manual review unchanged. Revised candidates should then re-enter the adjudication gate:

```text
revised_candidates.jsonl
→ adjudication_template.csv
→ adjudication_completed.csv
→ adjudicated_candidates.jsonl
```

### 6. Prepare manual-review input

Manual review should normally receive only candidates that are ready for human final review. If using adjudication, filter `adjudicated_candidates.jsonl` to records where:

```text
adjudication.pilot_ready_for_manual_review == true
```

A common artefact name is:

```text
<out>/ready_for_manual_review.jsonl
```

This is a curation artefact. It should contain only the cases you intend to manually approve/reject as final pilot candidates.

### Human gate C: complete manual review CSV

Manual review is the final pilot-selection gate.

Required fields:

```text
manual_decision = keep | revise | reject
manual_reason
required_edits
phase3_pilot_candidate = true | false
```

Only manual review may set:

```text
manual_review.phase3_pilot_candidate = true
```

Adjudication readiness is not pilot approval.

### 7. Apply manual review

This step is local and makes no model calls.

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --apply-manual-review `
  --out data/generated/phase3_core_overapproval_v2 `
  --review-input-jsonl data/generated/phase3_core_overapproval_v2/ready_for_manual_review.jsonl `
  --manual-review-csv data/generated/phase3_core_overapproval_v2/manual_review_completed.csv `
  --pilot-output-jsonl data/generated/phase3_core_overapproval_v2/phase3_pilot_candidates.jsonl
```

Output:

```text
<out>/phase3_pilot_candidates.jsonl
```

Candidates with deterministic validation errors are excluded unless `--allow-reviewed-validation-errors` is supplied deliberately.

### 8. Merge approved pilot files, if needed

If several curated runs produce pilot files, merge them:

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --merge-pilot-candidates `
  --pilot-inputs `
    data/generated/phase3_core_overapproval_v1/phase3_pilot_candidates.jsonl `
    data/generated/phase3_core_overapproval_v2/phase3_pilot_candidates.jsonl `
  --pilot-output-jsonl data/generated/phase3_core_overapproval_merged/phase3_pilot_candidates.jsonl
```

Identical duplicate `case_id` records are retained once. Conflicting records with the same `case_id` fail clearly.

### 9. Export to behaviour.py / Inspect JSONL shape

```powershell
python .\scripts\generate_phase3_dataset_candidates.py `
  --export-inspect-jsonl `
  --pilot-input-jsonl data/generated/phase3_core_overapproval_merged/phase3_pilot_candidates.jsonl `
  --inspect-output-jsonl data/datasets/phase3/phase3_core_overapproval_pilot_v2.jsonl `
  --dataset-version phase3_core_overapproval_pilot_v2
```

Only records with attached non-reject manual review and `phase3_pilot_candidate=true` should export.

### 10. Register dataset in behaviour.py

Add the new durable dataset path and `DATASET_CONFIGS` entry in:

```text
src/moral_eval/behaviour.py
```

Do not overwrite existing dataset versions. New pilots should get new dataset_version values.

### 11. Run one smoke eval

Example:

```powershell
inspect eval .\src\moral_eval\behaviour.py@moral_reasoning_integrity_behaviour `
  -T dataset_version=phase3_core_overapproval_pilot_v2 `
  --model openai/gpt-4o-mini `
  --limit 1
```

Do not run a broad model suite until the pilot cases and scoring rubric are stable.

### 12. Ingest the smoke log into Postgres

Dry-run first:

```powershell
$env:MORAL_EVALS_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/moral_evals"

python .\scripts\backfill_inspect_logs_to_postgres.py `
  --log-dir .\logs\<smoke-log>.eval `
  --dry-run
```

Then write/promote:

```powershell
python .\scripts\backfill_inspect_logs_to_postgres.py `
  --log-dir .\logs\<smoke-log>.eval `
  --write `
  --promote-operational
```

Verify that `public.inspect_log_sample` links to `eval_case_id` and `response_id`, and that reporting views are queryable.

## Artefact contract

| Artefact | Producer | Human gate? | Meaning |
| --- | --- | --- | --- |
| `raw_candidates.jsonl` | generation | no | Raw generated candidates before QC/filtering. |
| `scored_candidates.jsonl` | QC judge | no | Candidates with LLM QC scores. |
| `filtered_candidates.jsonl` | deterministic/QC filtering | no | Candidates after validation and quality thresholds. |
| `kept_candidates.jsonl` | dedupe/filter pipeline | no | Candidate pool eligible for adjudication, not pilot data. |
| `manual_review_template.csv` | generation pipeline | yes | Early manual-review template; may be bypassed if adjudication is used first. |
| `adjudication_template.csv` | adjudication export | yes | Editable pre-review quality-gate worksheet. |
| `adjudication_completed.csv` | human | yes | Completed adjudication provenance. |
| `adjudicated_candidates.jsonl` | adjudication apply | no | Records with adjudication metadata; reject rows excluded by default. |
| `adjudication_summary.json` | adjudication apply | no | Counts for keep/revise/reject and readiness. |
| `revise_candidates.jsonl` | revision extract | no | Candidate records whose adjudication verdict was revise. |
| `revision_notes.csv` | revision extract / human | yes | Editable worksheet for actual candidate revisions. |
| `revised_candidates.jsonl` | revision apply | no | Revised records ready for re-adjudication. |
| `revised_adjudication_template.csv` | revised-adjudication preparation | yes | Separate worksheet for changed candidates. |
| `revised_adjudication_completed.csv` | human | yes | Completed second adjudication. |
| `adjudicated_revised_candidates.jsonl` | revised-adjudication apply | no | Revised records retained after second adjudication. |
| `revised_adjudication_summary.json` | revised-adjudication apply | no | Second-pass keep/revise/reject counts. |
| `ready_for_manual_review.jsonl` | manual-review preparation | no | Original keeps plus revised second-pass keeps. |
| `manual_review_gate_template.csv` | manual-review preparation | yes | Final pilot-selection worksheet template. |
| `unresolved_for_manual_review.jsonl` | manual-review preparation | no | Second-pass revise cases requiring escalation. |
| `manual_review_preparation_summary.json` | manual-review preparation | no | Ready, unresolved, and excluded counts. |
| `manual_review_completed.csv` | human | yes | Final human pilot-selection worksheet. |
| `reviewed_candidates.jsonl` | manual-review apply | no | Explicitly approved candidate records used by final export. |
| `manual_review_summary.json` | manual-review apply | no | Ready, reviewed, and excluded counts. |
| Manifest `export.output_path` | `export_inspect_jsonl` | no | Final Inspect-compatible dataset JSONL. |
| `inspect_export_summary.json` | `export_inspect_jsonl` | no | Dataset version and export item counts. |
| `data/datasets/phase3/*.jsonl` | final export/promotion | no | Durable registered dataset file consumed by `behaviour.py`. |
| `*.metadata.json` | final promotion | no | Dataset-level provenance sidecar. |
| `logs/*.eval` | Inspect | no | Eval execution log. |
| Postgres rows | backfill/promote | no | Queryable run/sample/response provenance. |

## Human gates summary

There are four explicit human gates:

```text
A. Adjudication
   Decides whether a candidate is ready for manual review, needs revision, or should be rejected.

B. Revision
   Makes actual candidate-field changes for promising but not-ready cases.

B2. Revised adjudication
   Re-assesses changed candidates without reusing the stale original decision.

C. Manual review
   Grants or denies final pilot inclusion.
```

Only gate C can set `phase3_pilot_candidate=true`.

## Guardrails

Do not expand datasets merely to increase sample count. Prefer hypothesis-driven cells and small batches.

Do not treat broad model results from tiny diagnostic datasets as benchmark results.

Do not merge Phase 1 continuity-suite results, Phase 2 contemporary-suite results, and Phase 3 pilot work into one undifferentiated result claim.

Do not put generated candidates, adjudication readiness, or QC judge approval directly into `data/datasets/phase3/` without final human review and smoke testing.

Do not run broad model suites until the Phase 3 pilot cases and scoring rubric are stable.
