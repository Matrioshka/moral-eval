# DB-backed dataset-generation operator guide

Run commands from the repository root.

## Configure the run

```powershell
$env:PYTHONPATH = "$PWD\src"
```

Configure `MORAL_EVALS_DATABASE_URL`, or the existing PostgreSQL environment variables. API credentials are not stored in the manifest.

Copy `docs/dataset_generation/example_manifest.yaml` and change the run slug, output directory, model names, dataset version, and export path. A registered slug is tied to its manifest hash; use a new slug after changing the manifest.

`safety.allow_model_calls` defaults to `false`. Set it explicitly to `true` only for an intentionally model-calling generation run.

## Seed briefs and generation cells

Seed briefs under `docs/dataset_generation/seeds/` may be used to translate a research topic into concrete, schema-valid generation cells under `data/generation_cells/`.

A debate or policy disagreement may help identify scenario families, evidence gaps, and pressure mechanisms, but it remains an upstream design aid. The generated eval task should test calibrated moral and safety reasoning under pressure, not reward a preferred debate position or persuasive performance.

## Initialise and inspect

```powershell
.\.venv\Scripts\python.exe .\scripts\moral_gen.py init .\path\to\manifest.yaml
.\.venv\Scripts\python.exe .\scripts\moral_gen.py status <run-slug>
.\.venv\Scripts\python.exe .\scripts\moral_gen.py artifacts <run-slug>
```

`init` validates and snapshots the manifest, applies migration 023 idempotently, creates stage rows, and records the manifest and cells artefacts.

## Advance one stage

```powershell
.\.venv\Scripts\python.exe .\scripts\moral_gen.py next <run-slug>
```

Each call advances at most one stage. Check status and artefacts after each call. The artefact command reports paths, hashes, row and byte counts, human-edited state, and filesystem drift.

## Complete human gates

When `next` opens a gate, it prints the exact template and expected completed path. Copy and edit only the file requested by the current consuming stage:

```powershell
Copy-Item <adjudication_template.csv> <adjudication_completed.csv>
Copy-Item <revision_notes.csv> <revision_notes_completed.csv>
Copy-Item <revised_adjudication_template.csv> <revised_adjudication_completed.csv>
Copy-Item <manual_review_gate_template.csv> <manual_review_completed.csv>
```

Then run `next` again. Missing human input leaves the consuming stage pending; it is not treated as stage failure. Gates do not globally pause the run or set it to `waiting_human`.

## Revision policy

Only original adjudication verdicts of `revise` enter revision. Revised candidates must pass through the separate revised-adjudication gate.

There is one bounded revision cycle:

- revised `keep` → eligible for manual review;
- revised `reject` → excluded;
- revised `revise` → written to `unresolved_for_manual_review.jsonl` for human escalation.

No recursive revision loop is implemented.

## Manual review and export

`prepare_manual_review` writes:

```text
ready_for_manual_review.jsonl
manual_review_gate_template.csv
unresolved_for_manual_review.jsonl
manual_review_preparation_summary.json
```

`apply_manual_review` writes:

```text
reviewed_candidates.jsonl
manual_review_summary.json
```

`export_inspect_jsonl` consumes `reviewed_candidates.jsonl` only and writes:

```text
<manifest export.output_path>
<run output directory>/inspect_export_summary.json
```

This is a file export only. It does not run Inspect and does not register the dataset.

## Registration is separate

After independent review of the exported JSONL, dataset registration is a separate code change in `src/moral_eval/behaviour.py`. Successful export is not registration, evaluation, or Inspect-log ingest.
