# PostgreSQL schema separation dependency inventory

Date: 2026-06-08

Inputs:

- `docs/design/schema_dependency_scan.csv`
- `docs/design/sql_migration_inventory.md`

Status: dependency inventory and migration plan only. This document does not move tables, edit SQL files, mutate datasets, run model evals, or create `bootstrap_current_schema.sql`.

## Purpose

This document identifies repository references that matter for separating the current PostgreSQL provenance layer into three schemas:

- `raw` for imported/source-shaped provenance tables;
- `public` for operational tables;
- `rpt` for reporting/export/query views.

The dependency scan is intentionally broad and therefore noisy. Some hits are true PostgreSQL object dependencies. Others are ordinary dataset-field names, prose references, CSV column names, or a separate DuckDB schema that happens to use the same table name. This inventory separates those cases so the migration plan does not overfit to irrelevant matches.

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

`model_run` is a grey-zone dependency. It is imported run metadata, but public operational `response` rows currently link to it. Recommendation: treat it as `raw.model_run`, and make all operational/reporting joins schema-qualified.

`expected_behaviour` is also a noisy term because it is both a PostgreSQL table and a dataset JSONL/CSV field. Only SQL table references and SQL strings should be treated as PostgreSQL schema dependencies.

### Reporting candidates

- `case_run_trace`
- `case_run_trace_reporting`
- `score_linkage_status`

Recommendation: target these at `rpt.*`, with temporary public compatibility views during transition.

## Classification rules

- **active**: current script/helper likely to be run directly.
- **rebuild-only**: SQL or tooling needed to rebuild or repair the PostgreSQL layer from existing artefacts.
- **diagnostic**: read-only health-check, linkage inspection, or validation.
- **historical**: superseded migration or old repair path retained for auditability.
- **documentation**: Markdown, generated case cards, diagrams, examples, or prose.
- **unknown**: reference lacks enough context to classify safely.

A scan hit is counted as a true PostgreSQL dependency only when it appears in SQL DDL/DML, SQL strings embedded in scripts, or a documented SQL command. Dataset field names and prose are classified, but they do not block schema separation.

## High-level findings

The current breakage risk is concentrated in three places:

1. `scripts/ingest_eval_artifacts_to_postgres.py` writes to the unqualified raw/import tables and executes `sql/001_create_eval_provenance_schema.sql` for `--init-schema`.
2. The current rebuild path depends on unqualified raw/import tables in SQL files `001`, `003`, `004`, `005`, `009`, `011`, `013`, and `014`.
3. Active export/diagnostic scripts and documentation still query unqualified reporting views, especially `case_run_trace` and `score_linkage_status`.

The existing SQL migration inventory already says the SQL directory mixes a base provenance/import schema, additive operational normalisation, historical repair/backfill migrations, and diagnostic views. It recommends that a future bootstrap create the end-state objects directly and move historical repair scripts to `sql/legacy_migrations/` after validation. It also records a real rebuild gap: `002_populate_moral_domain_descriptions.sql` assumes a `moral_domain` table exists, but no current SQL file creates it.

## True PostgreSQL dependencies from the scan

| Path | Classification | Referenced objects/views | What breaks if raw tables move to `raw.*` and views move to `rpt.*` |
|---|---|---|---|
| `scripts/ingest_eval_artifacts_to_postgres.py` | active | `source_file`, `dataset`, `dataset_case`, `case_intervention`, `expected_behaviour`, `model_run`, `model_response`, `structured_decision_tuple`, `manual_score`, `deterministic_score`, `response_failure_class`; also `sql/001_create_eval_provenance_schema.sql` through `--init-schema` | This is the highest-risk active dependency. Inserts, upserts, selects, and truncates will fail if the raw tables move and the script remains unqualified. `--init-schema` would also recreate old public tables instead of creating `raw.*`, `public.*`, and `rpt.*`. `--reset-data` is not safe enough for a split schema because it truncates raw/import tables but does not explicitly clear all derived operational rows. |
| `scripts/diagnose_score_linkage.py` | diagnostic | `source_file`, `manual_score`, `model_response`, `model_run`, `deterministic_score`; also operational `response` and `score_event` | Read-only diagnostic queries fail when raw objects move. Update after ingest, but before removing public aliases. It should query `raw.source_file`, `raw.manual_score`, `raw.model_response`, `raw.model_run`, `raw.deterministic_score`, plus public operational tables. |
| `scripts/export_case_card.py` | active | `case_run_trace`; output columns expose `dataset_case`, `model_response`, `manual_score`, `deterministic_score` as source-file keys | Export fails if `case_run_trace` moves to `rpt.case_run_trace` and no public compatibility view exists. Update script to read `rpt.case_run_trace`; keep a temporary public view alias for old commands. |
| `sql/001_create_eval_provenance_schema.sql` | rebuild-only | Creates all current raw/import candidates except it also creates early `rubric`/`failure_class`; creates `case_run_trace` and `case_run_trace_reporting` in public | Core rebuild blocker. If unchanged, it recreates public raw tables and public reporting views. Future design should replace or fold it into a schema-qualified bootstrap. |
| `sql/003_create_and_populate_scenario.sql` | rebuild-only | Reads/writes `dataset_case`; references `source_file`; creates/populates public `scenario` | Fails if `dataset_case` and `source_file` move. Final version should write `public.scenario` but read/update `raw.dataset_case` deliberately, because `dataset_case.scenario_id` is still used as a bridge in the present model. |
| `sql/004_update_scenario_names.sql` | rebuild-only | Reads `dataset_case`; updates `scenario` | Fails if `dataset_case` moves. Keep as post-ingest normalisation or fold into later bootstrap/backfill logic. |
| `sql/005_create_operational_case_turn_model.sql` | rebuild-only | Reads `dataset`, `dataset_case`, `case_intervention`, `model_run`, `model_response`, `source_file`; writes public `evidence_quality`, `eval_case`, `case_turn`, `response` | Major rebuild dependency. It builds the operational layer from raw rows. It must become schema-qualified before raw tables move, or be replaced by a bootstrap/post-ingest backfill. |
| `sql/009_fix_turn_score_normalisation.sql` | rebuild-only | Reads `case_intervention`, `manual_score`, `deterministic_score`, `source_file`; writes/repairs public lookup/scoring objects | Active current repair/backfill dependency according to the migration inventory. Must read raw score tables with `raw.*` after the split. |
| `sql/011_backfill_response_score_coverage_deduped.sql` | rebuild-only | Reads `dataset_case`, `case_intervention`, `model_response`, `manual_score`, `deterministic_score`; writes public `eval_case`, `case_turn`, `response`, `score_event` | Rebuild-critical. It contains the deduplicated response/score-event backfill that supersedes `010`. Must be schema-qualified or folded into an explicit post-ingest backfill. |
| `sql/013_backfill_unresolved_legacy_response_turns.sql` | rebuild-only | Reads `dataset_case`, `model_response`, `manual_score`, `deterministic_score`; writes public placeholder turn and score backfill objects | Rebuild-critical. It preserves the important `unresolved_legacy_prompt` semantics. Must use `raw.*` for imported response/score tables. |
| `sql/014_create_score_linkage_status_view.sql` | diagnostic / rebuild-only | Creates `score_linkage_status`; reads `manual_score`, `model_response`, `model_run`, `source_file`, plus operational `response`, `score_event`, `failure_class` | Should become `rpt.score_linkage_status`. A temporary `public.score_linkage_status` compatibility view is useful because docs and ad hoc diagnostics currently use the public name. |
| `docs/postgres_provenance_layer.md` | documentation | Example SQL over `dataset_case`, `dataset`, `source_file`, `case_run_trace`, `score_linkage_status`; prose references to `model_response`, `manual_score`, `deterministic_score`, `model_run` | Documentation becomes wrong after the split. Update only after implementation changes are real, not before. |
| `docs/diagrams/postgres_operational_physical_er_model.md` | documentation | Describes the operational chain and raw/import lineage tables including `dataset`, `dataset_case`, `source_file`, `model_response`, `manual_score`, `deterministic_score` | Diagram and notes need updating after the split. No runtime breakage. |
| `docs/diagrams/postgres_operational_relationships.mmd` | documentation | ER diagram references import and operational tables | Diagram source needs schema-aware labels after the split. No runtime breakage. |
| `docs/case_cards/scope_control_military_strong_authority_urgency.md` | documentation / generated artefact | States it was exported from `case_run_trace`; contains rendered `manual_score`, `deterministic_score`, and source-file labels | Historical/generated artefact. No code breakage. Future generated cards should say `rpt.case_run_trace` once the view moves. |

## Historical SQL references that should not drive the new design

These files contain real PostgreSQL object references, but the migration inventory classifies them as superseded or historical. They should be retained for audit history until a clean bootstrap exists, but they should not receive compatibility work beyond archival needs.

| File | Classification | Why it should not drive the design |
|---|---|---|
| `sql/006_create_turn_pressure_failure_lookups.sql` | historical | Early lookup-table creation. Later scripts defensively recreate and refine these lookups. Fold final lookup definitions into bootstrap, not this early version. |
| `sql/007_normalise_turns_and_scores.sql` | historical | First attempt to normalise turn metadata and create/populate `score_event`. Superseded by later repairs. |
| `sql/008_repair_normalise_turns_and_scores.sql` | historical | Repair of `007`, superseded by `009`. |
| `sql/010_backfill_all_operational_responses_and_scores.sql` | historical | Broader backfill attempt superseded by deduplicated `011`. |

Do not add public compatibility views merely to keep these historical scripts runnable. That would be designing the future around the sedimentary layer. Sediment is useful to geologists; databases should aspire to less of it.

## Rebuild-critical SQL references

The current rebuild-critical path, from `sql_migration_inventory.md`, is:

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

Not all of these appear heavily in the scan, because `002` and `012` do not reference the raw/import candidate list much. They are still rebuild-critical because `002` seeds `moral_domain`, and `012` patches `rubric` timestamps for later score backfills.

Important bootstrap implication: future schema creation must define `public.moral_domain` before applying the domain seed data. The current inventory explicitly identifies this as a gap.

## Active scripts to update first

1. `scripts/ingest_eval_artifacts_to_postgres.py`

   Update first. It is the only active writer into the raw/import provenance layer and the only script that currently applies `001` through `--init-schema`. Add schema-qualified SQL or central schema-name constants before moving any table. Also split or clarify reset semantics before migration.

2. `scripts/export_case_card.py`

   Update second. It should read `rpt.case_run_trace`, not unqualified `case_run_trace`. This is a small change but protects a user-facing export path.

3. `scripts/diagnose_score_linkage.py`

   Update third. It is read-only, but it queries several raw/import tables and is important for preserving the manual-score ambiguity boundary.

4. Health-check / ad hoc PostgreSQL scripts

   `scripts/test_postgres_provenance_layer.ps1` was not prominent in the scan excerpt, but it is part of the active PostgreSQL workflow and should be updated to check `raw`, `public`, and `rpt` explicitly. It should become the main post-migration smoke test.

## Scan hits that are not PostgreSQL schema dependencies

The following references should be classified, but should not block schema separation:

| Path or group | Classification | Reason |
|---|---|---|
| `scripts/export_dataset_to_duckdb.py` | active, non-PostgreSQL | Creates and queries a DuckDB table named `source_file`. This is a separate local DuckDB analysis database, not the PostgreSQL provenance layer. It also has `expected_behaviour` as a dataset column. No PostgreSQL schema action needed. |
| `scripts/summarise_v4_scope_control_scores.py` | active, non-PostgreSQL | Uses `source_file` as a Python dataclass/CSV field. No PostgreSQL object dependency. |
| `scripts/apply_v4_pilot_manual_scores.py` | active or historical utility, non-PostgreSQL from scan context | Scan hit is ordinary prose containing `dataset`. No PostgreSQL object dependency shown. |
| `scripts/validate_release_governance_schema_v2.py` and `scripts/validate_release_governance_schema_v2_1.py` | diagnostic, non-PostgreSQL | `expected_behaviour` appears as a JSON/dataset field name, not a table reference. |
| `src/moral_sycophancy_eval/behaviour.py` | active eval code, non-PostgreSQL | `dataset` and `expected_behaviour` are Inspect/dataset concepts. This should not be changed for database schema separation. |
| `src/moral_sycophancy_eval/behaviour_schema_v2_1_scored.py` | active eval code, non-PostgreSQL | Uses Inspect `json_dataset`; no PostgreSQL object dependency. |
| `src/moral_sycophancy_eval/compare_manual_vs_inspect_scores.py` | analysis/diagnostic, non-PostgreSQL | `manual_score` appears as a variable/CSV concept. No PostgreSQL object dependency. |
| `src/moral_sycophancy_eval/export_behaviour_outputs.py` | active export utility, non-PostgreSQL | `expected_behaviour` is an output/audit field, not the PostgreSQL table. |
| `src/moral_sycophancy_eval/integrity.py`, `recognition.py`, `summarise_results.py`, `summarise_manual_scores.py`, `validate_dataset.py`, `schema_v2_1_scorer.py` | active/diagnostic eval utilities, non-PostgreSQL | Scan hits are dataset-field names, Inspect dataset loading, or prose around manual scoring. No PostgreSQL schema dependency. |
| Dataset specs, reports, releases, failure audits, project brief, run notes, and article drafts | documentation | Most scan hits are prose uses of `dataset`, `expected_behaviour`, `manual_score`, or `failure_class`. They do not affect PostgreSQL migration except where they contain explicit SQL examples, mainly in `docs/postgres_provenance_layer.md`. |

No `unknown` runtime dependency remains after review of the scan context. Some broad documentation references are semantically vague, but they are documentation-only, not database runtime dependencies.

## What would break if objects moved immediately

If the raw/import tables were moved to `raw.*` and reporting views to `rpt.*` immediately, without code and SQL changes:

- `scripts/ingest_eval_artifacts_to_postgres.py` would fail on unqualified inserts/upserts/selects/truncates against raw/import tables.
- `--init-schema` would recreate the old public import tables and public reporting views from `001`, undermining the split.
- `--reset-data` would be unsafe or wrong because its current truncation list is not split-schema aware and does not explicitly handle all derived operational objects.
- `sql/003`, `004`, `005`, `009`, `011`, `013`, and `014` would fail or read the wrong objects unless raw references were schema-qualified.
- `case_run_trace` and `case_run_trace_reporting` would fail unless recreated against schema-qualified raw/public objects.
- `scripts/export_case_card.py` would fail unless changed to `rpt.case_run_trace` or given a temporary `public.case_run_trace` compatibility view.
- `scripts/diagnose_score_linkage.py` would fail unless changed to schema-qualified raw/public objects or given compatibility aliases.
- documented SQL examples in `docs/postgres_provenance_layer.md` would become wrong.

## Compatibility view recommendation

### Reporting views

Use public compatibility views during transition:

- `public.case_run_trace` -> `rpt.case_run_trace`
- `public.case_run_trace_reporting` -> `rpt.case_run_trace_reporting`
- `public.score_linkage_status` -> `rpt.score_linkage_status`

These are justified because active scripts, documentation, and ad hoc queries currently use the public names. They are read-only surfaces, so compatibility views are low risk.

### Raw/import tables

Avoid public compatibility views for raw/import tables if possible.

Reason: the most important raw dependency is the ingest script, which performs inserts, upserts, conflicts, and truncates. Simple PostgreSQL views are not a clean substitute for those behaviours, especially with `ON CONFLICT`, foreign keys, identity columns, cascades, and reset semantics. Raw-table public aliases could conceal bugs rather than prevent them.

Preferred approach: update the active writer and rebuild SQL to use `raw.*` before moving tables. If a narrow temporary alias is unavoidable, document it with a removal condition and do not rely on it for the ingest/reset path.

## Ordered schema-separation plan

1. Keep the current working provenance layer intact.

   Do not move tables, edit SQL migrations, mutate datasets, run evals, or create `bootstrap_current_schema.sql` yet.

2. Update active scripts to be schema-aware.

   Start with `scripts/ingest_eval_artifacts_to_postgres.py`, then `scripts/export_case_card.py`, then `scripts/diagnose_score_linkage.py`, then the health-check PowerShell script. Use constants or config defaults for `raw`, `public`, and `rpt` rather than scattering literal schema names.

3. Redesign reset semantics before any move.

   Replace or clarify `--reset-data`. Suggested future split:

   - raw-only reset/re-ingest;
   - derived operational rebuild;
   - full rebuild, with explicit dependency order.

   The current single reset flag is too vague once raw and derived layers are separated.

4. Prepare `rpt` views first.

   When SQL changes are allowed, create `rpt.case_run_trace`, `rpt.case_run_trace_reporting`, and `rpt.score_linkage_status`, then add temporary public compatibility views.

5. Schema-qualify the rebuild-critical SQL path.

   Update or replace the active rebuild path so it reads imported objects from `raw.*`, writes operational objects to `public.*`, and creates views in `rpt.*`. Do not make historical files `006`, `007`, `008`, or `010` drive the new design.

6. Move or rebuild raw/import tables.

   Prefer a clean rebuild into `raw` from source artefacts once scripts are ready. This project treats files as the source of truth, so a verified rebuild is cleaner than a fragile table-shuffle. Preserve source-file hashes and row-count invariants.

7. Validate using database invariants only.

   No model evals are needed. Compare:

   - `source_file` count and hashes;
   - dataset/case counts by dataset version;
   - non-blank legacy `model_response` rows vs operational `response` rows;
   - manual and deterministic score linkage counts;
   - reporting trace row counts;
   - reporting guard checks for smoke, summary, expansion-candidate, and rewrite-candidate artefacts;
   - a known case-card export.

8. Update documentation and diagrams after implementation.

   Update `docs/postgres_provenance_layer.md`, the SQL migration inventory, ER diagrams, and generated/export notes only after the database and scripts actually use the split. Documentation-first migration is how future you gets mugged by past you.

9. Create `bootstrap_current_schema.sql` last.

   Only after the split is validated should a clean bootstrap be written. It should define the end-state directly:

   - `raw` import/source tables;
   - `public` operational tables and lookup/rubric tables;
   - `rpt` reporting and diagnostic views;
   - indexes, constraints, triggers, and seed data;
   - the missing `public.moral_domain` definition;
   - final `rubric` and `failure_class` definitions;
   - no historical repair detritus.

Data-population logic that depends on ingested artefact rows should probably live in an explicit post-ingest backfill script rather than being mixed into pure DDL.

## Bottom line

Do not move raw tables yet. First make the active writer and active readers schema-aware, then move reporting views behind public compatibility aliases, then migrate or rebuild raw tables, then validate. Only after that should `bootstrap_current_schema.sql` exist.
