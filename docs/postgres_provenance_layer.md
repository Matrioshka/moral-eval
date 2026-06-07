# PostgreSQL provenance layer

This project still treats repository files as the source of truth. PostgreSQL is a queryable provenance/index layer over those files so that a case can be traced from dataset item to model output, structured tuple, manual audit, deterministic score, and source-file lineage.

It is not a replacement for Inspect logs, JSONL datasets, manual audit CSVs, or Markdown reports.

## Connection configuration

Do not commit database credentials. The ingest script accepts connection details in one of three ways:

1. `MORAL_EVALS_DATABASE_URL`
2. libpq-style environment variables: `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
3. `--dsn`, for local ad hoc use only

For local Docker development:

```powershell
python -m pip install "psycopg[binary]"

$env:PGHOST = "localhost"
$env:PGPORT = "5432"
$env:PGDATABASE = "moral_evals"
$env:PGUSER = "postgres"
$env:PGPASSWORD = "postgres"
```

The repository should not contain a default password-bearing connection string. `.env` should remain ignored.

## Initialise

If `psql` is installed locally:

```powershell
psql -h $env:PGHOST -p $env:PGPORT -U $env:PGUSER -d $env:PGDATABASE -f .\sql\001_create_eval_provenance_schema.sql
```

If PostgreSQL is running in the `postgres-dev` Docker container and local `psql` is not on `PATH`:

```powershell
Get-Content .\sql\001_create_eval_provenance_schema.sql | docker exec -i postgres-dev psql -U postgres -d moral_evals
```

The ingest script can also apply the schema:

```powershell
python .\scripts\ingest_eval_artifacts_to_postgres.py --init-schema
```

## Source plan

The ingest script uses an allowlisted source plan. It does not walk the whole repository.

Trace-bearing sources:

- `data/*.jsonl`
- `tmp/*export*.csv`
- `docs/failure_audits/*.csv`
- `docs/run_exports/**/*.csv`
- `artefacts/run_exports/**/*.csv`
- `results/**/*.csv`

Source-lineage-only sources:

- `docs/reports/*.md`
- `docs/releases/*.md`
- `results/**/*.md`
- `results/**/*.sql`
- `results/**/*.eval`

CSV files are only turned into trace rows when their headers look like case-level export or audit rows: they need a case key such as `sample_id`, `id`, `case_id`, or `source_item_id`, plus a payload field such as `output`, `raw_response`, `manual_score_0_to_3`, `primary_failure_class`, or `score`. Other CSV files are recorded in `source_file` only. This prevents summary CSVs from being misread as model responses.

Preview the source plan without connecting to PostgreSQL:

```powershell
python .\scripts\ingest_eval_artifacts_to_postgres.py --list-sources
```

## Rebuild from files

For a clean rebuild of the provenance layer from the current file tree:

```powershell
python .\scripts\ingest_eval_artifacts_to_postgres.py --init-schema --reset-data --yes
```

`--reset-data` truncates the provenance tables and re-ingests from the allowlisted files. It does not delete repository files, datasets, CSVs, reports, or Inspect outputs.

Use this when moving between computers or after copying curated exports/audits from another machine. It is usually cleaner than trying to diagnose stale rows.

## Ingest without reset

```powershell
python .\scripts\ingest_eval_artifacts_to_postgres.py
```

The ingest is safe to re-run. It upserts source files, datasets, cases, responses, and scores where possible.

## Query

```powershell
docker exec -i postgres-dev psql -U postgres -d moral_evals -c "select dataset_version, count(*) from dataset_case join dataset using (dataset_id) group by dataset_version order by dataset_version;"
```

```powershell
docker exec -i postgres-dev psql -U postgres -d moral_evals -c "select file_kind, file_path, record_count from source_file order by file_path;"
```

```powershell
docker exec -i postgres-dev psql -U postgres -d moral_evals -c "select case_id, model_name, run_label, manual_score, failure_class from case_run_trace where evidence_quality = 'strong_but_incomplete_safeguard' and pressure_type = 'urgency_deployment' and manual_score = 1 and failure_class ilike '%OVERAPPROVAL%';"
```

## Current provenance guarantees and limitations

The current operational chain is intended to support:

```text
dataset_case -> eval_case -> case_turn -> response -> score_event
```

Current guarantees after the latest sanity check:

- Every non-blank legacy `model_response.raw_response` row has an operational `response` row.
- `score_event` remains response-level: it links to `response`, not directly to dataset cases, source files, or run summaries.
- Linked manual scores preserve their original `manual_score` row through `score_event.legacy_manual_score_id`.
- Source-file lineage is retained through `source_file` and through the `source_files` object exposed by `case_run_trace`.
- `unresolved_legacy_prompt` turns are explicitly marked as placeholder turns when exact source prompt text could not be reconstructed.
- `case_run_trace_reporting` excludes smoke, summary, expansion-candidate, and rewrite-candidate artefacts where the current filename-based filters identify them.

Current known limitations:

- 233 legacy `manual_score` rows remain outside `score_event` because they are not safely auto-linkable under current evidence.
- The remaining unlinked manual scores usually point to blank/stub legacy `model_response` rows, while plausible real response candidates are ambiguous.
- Deterministic summary CSV rows are not response-level scores and should not be forced into `score_event`.
- The current reporting-safe trace is anchored in `*_manual_scores.csv` artefacts for all reporting rows, because those rows contain the usable response text and audit fields currently selected by the reporting filters.
- Some source rows are intentionally recorded as lineage-only artefacts rather than trace rows.

These are provenance constraints, not merely implementation annoyances. Do not relax them just to make counts look complete.

## Score Linkage Diagnostics

Use the score-linkage diagnostic when checking legacy `manual_score` rows that
have not become response-level `score_event` rows:

```powershell
python .\scripts\diagnose_score_linkage.py
```

The script connects with the same local PostgreSQL configuration conventions as
the ingest tooling: `MORAL_EVALS_DATABASE_URL`, or `PGHOST`, `PGPORT`,
`PGDATABASE`, `PGUSER`, and `PGPASSWORD`. It sets the active database transaction
to read-only, does not create score events, does not run evals, and does not
change source datasets or audit files.

Generated reports are written under `tmp/score_linkage_diagnostics/`:

- `unlinked_manual_scores_by_file.csv`
- `candidate_response_counts.csv`
- `uniquely_linkable_manual_scores.csv`
- `ambiguous_manual_scores.csv`
- `missing_output_file_pairs.csv`
- `summary.md`

The diagnostic is deliberately conservative. It does not propose a link by
`case_id` or `sample_id` alone when more than one response candidate exists. It
requires exact model, dataset version, prompt style, and source-file pairing when
those fields are available. Row-order matches are reported only when the
manual-score file and expected output file are both present in `source_file` and
have identical row counts.

Current finding from the diagnostic run:

- 233 remaining unlinked `manual_score` rows were inspected.
- 0 are safely auto-linkable under the current evidence.
- The remaining rows are ambiguous or unmatched.
- Deterministic summary CSV rows should not be forced into response-level
  `score_event`; they are summary diagnostics rather than human manual-audit
  rows for a single model response.

Keep generated CSV diagnostics under `tmp/` unless a small output is clearly
useful as a documentation artefact.

## Score linkage status view

Apply the linkage-status view after schema initialisation if it is not already present:

```powershell
Get-Content .\sql\014_create_score_linkage_status_view.sql |
  docker exec -i $env:PGCONTAINER psql -U $env:PGUSER -d $env:PGDATABASE
```

Inspect linked and unlinked manual-score rows:

```powershell
docker exec -i $env:PGCONTAINER psql -U $env:PGUSER -d $env:PGDATABASE -c "
select linkage_status, count(*)
from score_linkage_status
group by linkage_status
order by linkage_status;
"
```

Inspect unlinked rows by source file:

```powershell
docker exec -i $env:PGCONTAINER psql -U $env:PGUSER -d $env:PGDATABASE -c "
select manual_score_source_path, linkage_status, count(*)
from score_linkage_status
where is_linked_to_score_event is false
group by manual_score_source_path, linkage_status
order by count(*) desc, manual_score_source_path;
"
```

`score_linkage_status` is diagnostic. It does not change the definition of `score_event` and should not be used to treat ambiguous legacy scores as if they were response-linked.

## Export a case card

```powershell
python .\scripts\export_case_card.py --case-id release_schema_v2_1_api_no_finetune_urgency --out .\docs\case_cards\release_schema_v2_1_api_no_finetune_urgency.md
```

The exporter currently selects one row by `--case-id`, `--sample-id`, or `--response-id`. Use SQL against `case_run_trace` first when you need to find a case by evidence quality, pressure type, score, or failure class.

The case-card exporter labels exact source text separately from the diagram-ready paraphrase.

A curated demonstration card is available at:

```text
docs/case_cards/scope_control_military_strong_authority_urgency.md
```

It illustrates strong-but-incomplete evidence under authority/urgency pressure in military decision support, with a calibrated scope-control response.

## Limitations

This is a first-pass provenance layer.

- It does not parse Markdown reports into structured claims.
- It does not read raw Inspect `.eval` logs directly; `.eval` files are currently recorded as lineage only.
- Model names and run labels are inferred from CSV filenames where the CSV itself does not record them.
- Manual scores are attached to the response row created from the same audit CSV. If a separate raw export and manual audit represent the same run but have different filenames, they may appear as separate `model_run` rows.
- Structured tuple extraction is best-effort.
- The case-card exporter selects by case, sample, or response rather than exposing the full SQL filter surface.
- The database is not yet a web app or dashboard.
- The ingest is file-driven and additive by default; use `--reset-data --yes` for a clean rebuild from the current source plan.
