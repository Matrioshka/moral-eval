# PostgreSQL schema separation dependency inventory

Date: 2026-06-08

Inputs:

- `docs/design/schema_dependency_scan.csv`
- `docs/design/sql_migration_inventory.md`

Status: dependency inventory, transition notes, and migration plan. Reporting views have now been split into `rpt` by `sql/015_create_rpt_reporting_views.sql`. This document does not move raw/import tables, mutate datasets, run model evals, or create `bootstrap_current_schema.sql`.

## Purpose

This document identifies repository references that matter for separating the PostgreSQL provenance layer into three schemas:

- `raw` for imported/source-shaped provenance tables;
- `public` for operational tables;
- `rpt` for reporting/export/query views.

The dependency scan is intentionally broad and noisy. Some hits are true PostgreSQL object dependencies. Others are dataset-field names, prose references, CSV column names, or a separate DuckDB schema that happens to use the same name. This inventory separates those cases so the migration plan does not overfit to irrelevant matches.

## Candidate objects

### Raw/import candidates

- `source_file`
- `dataset`
- `dataset_case`
- `case_intervention`
- `expected_behaviour`
- `model_run`
- `model_response`
- `manual_score`
- `deterministic_score`
- `structured_decision_tuple`
- `response_failure_class`

`model_run` is a grey-zone dependency. It is imported run metadata, but public operational `response` rows currently link to it. Recommendation: treat it as a future `raw.model_run`, and make all operational/reporting joins schema-qualified.

`expected_behaviour` is noisy because it is both a PostgreSQL table and a dataset JSONL/CSV field. Only SQL table references and SQL strings should be treated as PostgreSQL schema dependencies.

### Reporting candidates

- `case_run_trace`
- `case_run_trace_reporting`
- `score_linkage_status`

Current status: these now exist under `rpt` after applying `sql/015_create_rpt_reporting_views.sql`. Public compatibility views are retained:

- `public.case_run_trace` -> `rpt.case_run_trace`
- `public.case_run_trace_reporting` -> `rpt.case_run_trace_reporting`
- `public.score_linkage_status` -> `rpt.score_linkage_status`

## Classification rules

- **active**: current script/helper likely to be run directly.
- **rebuild-only**: SQL or tooling needed to rebuild or repair the PostgreSQL layer from existing artefacts.
- **diagnostic**: read-only health-check, linkage inspection, or validation.
- **historical**: superseded migration or old repair path retained for auditability.
- **documentation**: Markdown, generated case cards, diagrams, examples, or prose.
- **unknown**: reference lacks enough context to classify safely.

A scan hit is counted as a true PostgreSQL dependency only when it appears in SQL DDL/DML, SQL strings embedded in scripts, or a documented SQL command. Dataset field names and prose are classified, but they do not block schema separation.

## Current transition status

The active PostgreSQL-facing scripts are schema-aware in a default-preserving way: raw, operational, and reporting schemas default to `public`, with optional CLI and environment-variable overrides.

`sql/015_create_rpt_reporting_views.sql` is the first actual schema-separation migration. It creates the `rpt` schema and moves the reporting/diagnostic views there while preserving public compatibility aliases. It does not move raw/import tables.

`script/ingest_eval_artifacts_to_postgres.py` now qualifies raw/import table references through helper functions for `source_file`, `dataset`, `dataset_case`, `case_intervention`, `expected_behaviour`, `model_run`, `model_response`, `manual_score`, `deterministic_score`, `structured_decision_tuple`, and `response_failure_class`. Operational lookup references touched by ingest, such as `rubric` and `failure_class`, are routed through the operational schema helper.

Non-public `--init-schema` and `--reset-data` usage remains intentionally blocked until the rebuild path and reset semantics are redesigned for a real raw/public split. This is still the main blocker for moving raw/import tables.

No datasets, eval artefacts, model evals, raw/import table moves, or `bootstrap_current_schema.sql` creation are part of this transition.

## True PostgreSQL dependencies from the scan

| Path | Classification | Referenced objects/views | Current migration impact |
|---|---|---|---|
| `scripts/ingest_eval_artifacts_to_postgres.py` | active | Raw/import candidates; operational `rubric` and `failure_class`; `sql/001_create_eval_provenance_schema.sql` through `--init-schema` | Now schema-aware and qualified, but `--init-schema` and `--reset-data` remain public-only/guarded. Raw table movement is still blocked by rebuild/reset semantics. |
| `scripts/diagnose_score_linkage.py` | diagnostic | `source_file`, `manual_score`, `model_response`, `model_run`, `deterministic_score`; operational `response`, `score_event`, `failure_class`; reporting schema option | Schema-aware. After `015`, diagnostics can use `--rpt-schema rpt` for reporting views where relevant. Raw tables still default to public. |
| `scripts/export_case_card.py` | active | `case_run_trace` | Schema-aware. After `015`, use `--rpt-schema rpt`; public compatibility view remains available. |
| `scripts/test_postgres_provenance_layer.ps1` | active / diagnostic | Raw counts, operational counts, reporting views | Schema-aware. Should be used as a post-migration smoke test for both public compatibility and `rpt` views. |
| `sql/001_create_eval_provenance_schema.sql` | rebuild-only | Creates current public raw/import tables and public `case_run_trace` / `case_run_trace_reporting` | Still a rebuild blocker. If run alone, it creates the older public-layout views. Apply `015` afterwards for the reporting split. Future bootstrap should replace/fold this. |
| `sql/003_create_and_populate_scenario.sql` | rebuild-only | Reads/writes `dataset_case`; references `source_file`; creates/populates public `scenario` | Still assumes public raw/import tables. Needs schema qualification before raw tables move. |
| `sql/004_update_scenario_names.sql` | rebuild-only | Reads `dataset_case`; updates `scenario` | Still assumes public `dataset_case`. Needs schema qualification before raw tables move. |
| `sql/005_create_operational_case_turn_model.sql` | rebuild-only | Reads `dataset`, `dataset_case`, `case_intervention`, `model_run`, `model_response`, `source_file`; writes public operational tables | Still a major raw-movement blocker. It builds the operational layer from raw rows. |
| `sql/009_fix_turn_score_normalisation.sql` | rebuild-only | Reads `case_intervention`, `manual_score`, `deterministic_score`, `source_file`; writes/repairs operational score objects | Needs raw qualification before raw tables move. |
| `sql/011_backfill_response_score_coverage_deduped.sql` | rebuild-only | Reads `dataset_case`, `case_intervention`, `model_response`, `manual_score`, `deterministic_score`; writes operational response/score rows | Rebuild-critical. Must be schema-qualified or folded into a post-ingest backfill before raw movement. |
| `sql/013_backfill_unresolved_legacy_response_turns.sql` | rebuild-only | Reads `dataset_case`, `model_response`, `manual_score`, `deterministic_score`; writes placeholder turns and score backfill objects | Rebuild-critical. Preserves `unresolved_legacy_prompt` semantics. Must be schema-qualified before raw movement. |
| `sql/014_create_score_linkage_status_view.sql` | diagnostic / rebuild-only | Creates public `score_linkage_status` | Remains for existing/public-layout rebuilds. Split-schema reporting is handled by `015`. |
| `sql/015_create_rpt_reporting_views.sql` | active / reporting migration | Creates `rpt.case_run_trace`, `rpt.case_run_trace_reporting`, `rpt.score_linkage_status`; creates public compatibility aliases | First actual schema split. Does not move raw/import tables. |
| `docs/postgres_provenance_layer.md` | documentation | SQL examples and workflow docs | Updated to document `015`, `rpt` views, and public compatibility aliases. |
| `docs/diagrams/postgres_operational_physical_er_model.md` and `docs/diagrams/postgres_operational_relationships.mmd` | documentation | ER references to import, operational, and reporting relationships | Still need a later diagram update if the physical schema split is presented visually. No runtime breakage. |
| `docs/case_cards/scope_control_military_strong_authority_urgency.md` | documentation / generated artefact | States it was exported from `case_run_trace` | Historical/generated artefact. Future generated cards should prefer `rpt.case_run_trace` after `015`. |

## Historical SQL references that should not drive the new design

These files contain real PostgreSQL object references, but the migration inventory classifies them as superseded or historical. They should be retained for audit history until a clean bootstrap exists, but they should not receive compatibility work beyond archival needs.

| File | Classification | Why it should not drive the design |
|---|---|---|
| `sql/006_create_turn_pressure_failure_lookups.sql` | historical | Early lookup-table creation. Later scripts defensively recreate and refine these lookups. Fold final lookup definitions into bootstrap, not this early version. |
| `sql/007_normalise_turns_and_scores.sql` | historical | First attempt to normalise turn metadata and create/populate `score_event`. Superseded by later repairs. |
| `sql/008_repair_normalise_turns_and_scores.sql` | historical | Repair of `007`, superseded by `009`. |
| `sql/010_backfill_all_operational_responses_and_scores.sql` | historical | Broader backfill attempt superseded by deduplicated `011`. |

Do not add public compatibility views merely to keep these historical scripts runnable. That would be designing the future around sediment. Sediment is useful to geologists; databases should aspire to less of it.

## Rebuild-critical SQL references

Current rebuild-critical path:

- `001_create_eval_provenance_schema.sql`
- `002_populate_moral_domain_descriptions.sql`
- `003_create_and_populate_scenario.sql`
- `004_update_scenario_names.sql`
- `005_create_operational_case_turn_model.sql`
- `009_fix_turn_score_normalisation.sql`
- `011_backfill_response_score_coverage_deduped.sql`
- `012_add_rubric_timestamps_for_score_backfill.sql`
- `013_backfill_unresolved_legacy_response_turns.sql`
- `014_create_score_linkage_status_view.sql`
- `015_create_rpt_reporting_views.sql`

`015` should be applied after the public-layout views/tables exist. It moves the reporting surface to `rpt` and retains public compatibility aliases.

Important bootstrap implication: future schema creation must define `public.moral_domain` before applying the domain seed data. The current inventory explicitly identifies this as a gap.

## Scan hits that are not PostgreSQL schema dependencies

| Path or group | Classification | Reason |
|---|---|---|
| `scripts/export_dataset_to_duckdb.py` | active, non-PostgreSQL | Creates and queries a DuckDB table named `source_file`. This is a separate local DuckDB analysis database, not the PostgreSQL provenance layer. It also has `expected_behaviour` as a dataset column. |
| `scripts/summarise_v4_scope_control_scores.py` | active, non-PostgreSQL | Uses `source_file` as a Python dataclass/CSV field. |
| `scripts/apply_v4_pilot_manual_scores.py` | active or historical utility, non-PostgreSQL from scan context | Scan hit is ordinary prose containing `dataset`. |
| `scripts/validate_release_governance_schema_v2.py` and `scripts/validate_release_governance_schema_v2_1.py` | diagnostic, non-PostgreSQL | `expected_behaviour` appears as a JSON/dataset field name, not a table reference. |
| `src/moral_sycophancy_eval/*` eval and summary utilities | active/diagnostic eval utilities, non-PostgreSQL | Scan hits are dataset-field names, Inspect dataset loading, or prose around manual scoring. |
| Dataset specs, reports, releases, failure audits, project brief, run notes, and article drafts | documentation | Most scan hits are prose uses of `dataset`, `expected_behaviour`, `manual_score`, or `failure_class`. They do not affect PostgreSQL migration except where they contain explicit SQL examples, mainly in `docs/postgres_provenance_layer.md`. |

No `unknown` runtime dependency remains after review of the scan context. Some broad documentation references are semantically vague, but they are documentation-only, not database runtime dependencies.

## What would break if raw/import tables moved immediately

Reporting views have now been split safely, but raw/import tables have not moved. If raw/import tables moved to `raw.*` immediately:

- `--init-schema` would still recreate the old public import tables from `001`, undermining the split.
- `--reset-data` would remain unsafe or wrong because reset semantics are not split-schema aware.
- `sql/003`, `004`, `005`, `009`, `011`, and `013` would fail or read the wrong objects unless raw references were schema-qualified.
- Existing operational foreign keys and lineage links into raw/import objects would need deliberate review.
- documented rebuild commands would become incomplete unless paired with a new split-schema bootstrap or post-ingest backfill.

## Compatibility view policy

### Reporting views

Reporting compatibility views now exist after `015`:

- `public.case_run_trace` -> `rpt.case_run_trace`
- `public.case_run_trace_reporting` -> `rpt.case_run_trace_reporting`
- `public.score_linkage_status` -> `rpt.score_linkage_status`

These are justified because active scripts, documentation, and ad hoc queries historically used the public names. They are read-only surfaces, so compatibility views are low risk.

### Raw/import tables

Avoid public compatibility views for raw/import tables if possible.

Reason: the ingest script performs inserts, upserts, conflicts, and truncates. Simple PostgreSQL views are not a clean substitute for those behaviours, especially with `ON CONFLICT`, foreign keys, identity columns, cascades, and reset semantics. Raw-table public aliases could conceal bugs rather than prevent them.

Preferred approach: update the active writer and rebuild SQL to use `raw.*` before moving tables. If a narrow temporary alias is unavoidable, document it with a removal condition and do not rely on it for the ingest/reset path.

## Ordered schema-separation plan

1. Keep the current working provenance layer intact.

   Do not mutate datasets, run evals, or create `bootstrap_current_schema.sql` yet.

2. Active scripts schema-aware.

   Completed for the current active PostgreSQL-facing scripts in a default-preserving way.

3. Reporting views in `rpt` with public aliases.

   Implemented by `sql/015_create_rpt_reporting_views.sql`.

4. Redesign reset semantics before raw/import movement.

   Replace or clarify `--reset-data`. Suggested future split:

   - raw-only reset/re-ingest;
   - derived operational rebuild;
   - full rebuild, with explicit dependency order.

   The current single reset flag is too vague once raw and derived layers are separated.

5. Schema-qualify the rebuild-critical SQL path.

   Update or replace the active rebuild path so it reads imported objects from `raw.*`, writes operational objects to `public.*`, and creates views in `rpt.*`. Do not make historical files `006`, `007`, `008`, or `010` drive the new design.

6. Move or rebuild raw/import tables.

   Prefer a clean rebuild into `raw` from source artefacts once scripts and rebuild SQL are ready. This project treats files as the source of truth, so a verified rebuild is cleaner than a fragile table-shuffle. Preserve source-file hashes and row-count invariants.

7. Validate using database invariants only.

   No model evals are needed. Compare:

   - `source_file` count and hashes;
   - dataset/case counts by dataset version;
   - non-blank legacy `model_response` rows vs operational `response` rows;
   - manual and deterministic score linkage counts;
   - `rpt` and public compatibility reporting trace row counts;
   - reporting guard checks for smoke, summary, expansion-candidate, and rewrite-candidate artefacts;
   - a known case-card export with `--rpt-schema rpt`.

8. Update diagrams after implementation.

   Update ER diagrams once the schema split is stable enough to present visually.

9. Create `bootstrap_current_schema.sql` last.

   Only after the split is validated should a clean bootstrap be written. It should define the end-state directly:

   - `raw` import/source tables;
   - `public` operational tables and lookup/rubric tables;
   - `rpt` reporting and diagnostic views;
   - deliberate public compatibility aliases, if still needed;
   - indexes, constraints, triggers, and seed data;
   - the missing `public.moral_domain` definition;
   - final `rubric` and `failure_class` definitions;
   - no historical repair detritus.

Data-population logic that depends on ingested artefact rows should probably live in an explicit post-ingest backfill script rather than being mixed into pure DDL.

## Bottom line

Reporting views have now moved to `rpt` with public compatibility aliases. Do not move raw/import tables yet. The next substantive step is to redesign reset/rebuild semantics and schema-qualify the rebuild-critical SQL path before any raw-table movement.
