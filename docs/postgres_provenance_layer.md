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

## Export a case card

```powershell
python .\scripts\export_case_card.py --case-id release_schema_v2_1_api_no_finetune_urgency --out .\docs\case_cards\release_schema_v2_1_api_no_finetune_urgency.md
```

The exporter currently selects one row by `--case-id`, `--sample-id`, or `--response-id`. Use SQL against `case_run_trace` first when you need to find a case by evidence quality, pressure type, score, or failure class.

The case-card exporter labels exact source text separately from the diagram-ready paraphrase.

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
