# PostgreSQL provenance layer

This project still treats repository files as the source of truth. PostgreSQL is a queryable provenance/index layer over those files so that a case can be traced from dataset item to model output, structured tuple, manual audit, deterministic score, and source-file lineage.

It is not a replacement for Inspect logs, JSONL datasets, manual audit CSVs, or Markdown reports.

## Initialise

```powershell
python -m pip install "psycopg[binary]"

$env:PGHOST = "localhost"
$env:PGPORT = "5432"
$env:PGDATABASE = "moral_evals"
$env:PGUSER = "postgres"
$env:PGPASSWORD = "postgres"
$env:MORAL_EVALS_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/moral_evals"

psql -h $env:PGHOST -p $env:PGPORT -U $env:PGUSER -d $env:PGDATABASE -f .\sql\001_create_eval_provenance_schema.sql
```

The ingest script can also apply the schema:

```powershell
python .\scripts\ingest_eval_artifacts_to_postgres.py --init-schema
```

## Ingest

```powershell
python .\scripts\ingest_eval_artifacts_to_postgres.py
```

The script indexes:

- `data/*.jsonl`
- `tmp/*export*.csv`
- `docs/failure_audits/*.csv`
- `docs/reports/*.md`
- `docs/releases/*.md`

Markdown files are recorded as source-file lineage only. They are not parsed into claims.

## Query

```powershell
psql -h localhost -p 5432 -U postgres -d moral_evals -c "select dataset_version, count(*) from dataset_case join dataset using (dataset_id) group by dataset_version order by dataset_version;"
```

```powershell
psql -h localhost -p 5432 -U postgres -d moral_evals -c "select case_id, model_name, run_label, manual_score, failure_class from case_run_trace where evidence_quality = 'strong_but_incomplete_safeguard' and pressure_type = 'urgency_deployment' and manual_score = 1 and failure_class ilike '%OVERAPPROVAL%';"
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
- It does not read raw Inspect `.eval` logs directly; it ingests existing CSV exports and audit files.
- Model names and run labels are inferred from CSV filenames where the CSV itself does not record them.
- Manual scores are attached to the response row created from the same audit CSV. If a separate raw export and manual audit represent the same run but have different filenames, they may appear as separate `model_run` rows.
- Structured tuple extraction is best-effort.
- The case-card exporter selects by case, sample, or response rather than exposing the full SQL filter surface.
- The database is not yet a web app or dashboard.
- The ingest is additive and non-destructive; it does not remove rows for files that have been deleted from the repository.
