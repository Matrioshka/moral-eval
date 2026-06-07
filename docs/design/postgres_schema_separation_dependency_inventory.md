# PostgreSQL schema separation dependency inventory

Date: 2026-06-08

Status: dependency inventory and migration plan only. This document does not move tables, create schemas, edit datasets, run model evals, or create `bootstrap_current_schema.sql`.

## Purpose

The current PostgreSQL provenance layer works, but its database objects are still mostly in `public`. The intended cleanup is to separate import-shaped provenance tables, operational tables, and reporting views without breaking the existing provenance workflow.

The immediate goal is to understand what depends on the current unqualified/public object names before any schema move is attempted. Moving the raw/import tables immediately would break active scripts, rebuild SQL, diagnostics, and documentation commands.

## Intended schema split

### `raw`

Imported/source-shaped provenance tables:

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

`model_run` is a grey-zone object. It is source-shaped run metadata, but operational `response` rows currently link to it. It can move to `raw`, but only if operational/reporting queries are schema-qualified or compatibility views exist first.

### `public`

Operational tables:

- `moral_domain`
- `scenario`
- `eval_case`
- `case_turn`
- `response`
- `turn_type`
- `pressure_type`
- `evidence_quality`
- `scorer`
- `score_event`
- `failure_class`
- `rubric`

`failure_class` and `rubric` require care. `001_create_eval_provenance_schema.sql` creates early/simple versions, while later operational migrations enrich them. In the target model they should be public operational lookup/rubric tables, not raw import tables.

`moral_domain` is an existing rebuild gap: `002_populate_moral_domain_descriptions.sql` assumes a `moral_domain` table exists, but the current SQL inventory already notes that no current `sql/*.sql` file creates it. The future bootstrap must define it explicitly.

### `rpt`

Reporting/export/query views:

- `case_run_trace`
- `case_run_trace_reporting`
- `score_linkage_status`

During transition, public aliases for these views may be justified because active scripts and docs currently read unqualified `case_run_trace`, `case_run_trace_reporting`, and `score_linkage_status`.

## Dependency inventory

Classification meanings:

- Active: used directly by current workflows or helper scripts.
- Rebuild-only: needed when reconstructing or repairing the PostgreSQL layer from existing artefacts.
- Diagnostic: read-only inspection, validation, or health-check surface.
- Historical: superseded migration or old repair path retained for auditability.
- Documentation: Markdown/diagram/case-card references or example commands.

## SQL files

| File | Classification | Current dependencies | Schema-separation impact |
|---|---:|---|---|
| `sql/001_create_eval_provenance_schema.sql` | Active / rebuild-only | Creates the current import-shaped tables in `public`: `source_file`, `dataset`, `dataset_case`, `case_intervention`, `expected_behaviour`, `model_run`, `model_response`, `structured_decision_tuple`, `rubric`, `failure_class`, `manual_score`, `response_failure_class`, `deterministic_score`. Also creates `case_run_trace` and `case_run_trace_reporting`. | This is the main object-definition dependency. If raw tables move first, `--init-schema` will recreate public tables instead of the intended split. Future bootstrap should replace this with schema-qualified raw/public/rpt objects. |
| `sql/002_populate_moral_domain_descriptions.sql` | Rebuild-only | Inserts/updates `moral_domain`. | Requires a public `moral_domain` table. It should remain operational/public. Future bootstrap must create the table before this seed data or fold the seed data into the bootstrap. |
| `sql/003_create_and_populate_scenario.sql` | Rebuild-only | Creates `scenario`; reads `dataset_case.scenario`; references `source_file`; adds `dataset_case.scenario_id`. | Breaks if `dataset_case` or `source_file` move to `raw` without qualification or aliases. The final version should create `public.scenario`, read from `raw.dataset_case`, and preserve the source FK intentionally. |
| `sql/004_update_scenario_names.sql` | Rebuild-only / documentation utility | Reads `scenario` and `dataset_case` to derive nicer `scenario.name` values. | Breaks if `dataset_case` moves without qualification. Can become a post-ingest normalisation script reading `raw.dataset_case` and writing `public.scenario`. |
| `sql/005_create_operational_case_turn_model.sql` | Active rebuild-only | Creates `evidence_quality`, `eval_case`, `case_turn`, `response`; reads `dataset_case`, `dataset`, `case_intervention`, `model_response`; references `model_run`, `source_file`, `scenario`, `moral_domain`. | Major dependency. It assumes raw/import tables are in the search path. Final version should create public operational tables and backfill from schema-qualified `raw.*` tables. |
| `sql/006_create_turn_pressure_failure_lookups.sql` | Historical | Creates early `turn_type`, `pressure_type`, `failure_class` lookup tables. | Superseded by later repair/normalisation scripts. Do not spend compatibility effort here except preserving it under legacy migration history. |
| `sql/007_normalise_turns_and_scores.sql` | Historical | Creates/patches lookup tables, `scorer`, `score_event`; reads `case_turn`, `case_intervention`, `manual_score`, `deterministic_score`, `rubric`, `source_file`, `response`. | Superseded. Moving raw tables would break it, but it should not drive the schema split. Keep as legacy history after bootstrap exists. |
| `sql/008_repair_normalise_turns_and_scores.sql` | Historical | Repairs `007`; reads `case_turn`, `case_intervention`, `manual_score`, `deterministic_score`, `response`, `scorer`, `rubric`. | Superseded. Keep for audit history; do not design aliases around it. |
| `sql/009_fix_turn_score_normalisation.sql` | Rebuild-only | Repairs/completes operational scoring after `008`; reads/writes operational lookup and score objects, and depends on legacy score tables. | Still part of the current safe rebuild path. Must be schema-qualified or folded into bootstrap/post-ingest backfill before raw tables move. |
| `sql/010_backfill_all_operational_responses_and_scores.sql` | Historical | Backfills `eval_case`, `case_turn`, `response`, `score_event` from `dataset_case`, `model_response`, `case_intervention`, `manual_score`, `deterministic_score`. | Explicitly superseded by `011`. Keep as historical. Do not preserve public aliases just for this file. |
| `sql/011_backfill_response_score_coverage_deduped.sql` | Rebuild-only | Deduplicated backfill from `dataset_case`, `model_response`, `case_intervention`, `manual_score`, `deterministic_score`; writes `eval_case`, `case_turn`, `response`, `score_event`. | Active current repair/backfill dependency. It must become schema-qualified or be folded into a future `bootstrap_after_ingest.sql`. |
| `sql/012_add_rubric_timestamps_for_score_backfill.sql` | Rebuild-only compatibility | Alters `rubric` timestamps and trigger. | Fold directly into the final public `rubric` definition. After bootstrap validation it can move to legacy. |
| `sql/013_backfill_unresolved_legacy_response_turns.sql` | Rebuild-only | Adds `unresolved_legacy_prompt`; backfills remaining valid legacy responses from `dataset_case` and `model_response`; imports `manual_score` and `deterministic_score` into `score_event`. | Active current repair/backfill dependency. Must read schema-qualified `raw.*` after the split. The placeholder-turn semantics should be preserved. |
| `sql/014_create_score_linkage_status_view.sql` | Diagnostic / reporting | Creates `score_linkage_status`; reads `manual_score`, `model_response`, `model_run`, `response`, `score_event`, `failure_class`, `source_file`. | Target should be `rpt.score_linkage_status`. A temporary `public.score_linkage_status` alias is reasonable because docs and diagnostics currently use the unqualified name. |

## Python and PowerShell scripts

| File | Classification | Current dependencies | Schema-separation impact |
|---|---:|---|---|
| `scripts/ingest_eval_artifacts_to_postgres.py` | Active | Writes `source_file`, `dataset`, `dataset_case`, `case_intervention`, `expected_behaviour`, `model_run`, `model_response`, `structured_decision_tuple`, `manual_score`, `deterministic_score`; reads `dataset_case`, `rubric`, `failure_class`; `--init-schema` executes only `sql/001_create_eval_provenance_schema.sql`; `--reset-data` truncates the import/provenance tables. | Highest-risk active dependency. If raw tables move now, ingest will fail or recreate public tables. `--reset-data` also needs redesign, because it currently truncates raw/import objects but not all derived operational objects. In the split design, reset should either be explicitly raw-only plus documented post-ingest rebuild, or a full rebuild that clears derived operational tables safely. |
| `scripts/export_case_card.py` | Active | Reads unqualified `case_run_trace`; exports one row to Markdown. | Breaks if the view moves to `rpt` without either updating the script or adding a `public.case_run_trace` alias. Prefer updating the script to `rpt.case_run_trace` and keeping a temporary public view alias for old commands. |
| `scripts/diagnose_score_linkage.py` | Diagnostic | Read-only. Reads `source_file`, `manual_score`, `score_event`, `model_response`, `model_run`, operational `response`, and source-file metadata. | Breaks if raw tables move without qualification or aliases. Should be updated to read `raw.*` plus public operational tables. It should remain read-only. |
| `scripts/test_postgres_provenance_layer.ps1` | Active / diagnostic | Checks public views `case_run_trace` and `case_run_trace_reporting`; counts `source_file`, `dataset_case`, `model_response`, `manual_score`, `deterministic_score`; writes local health-check outputs. | Breaks if the views move to `rpt` and raw tables move to `raw` without query updates. This should become the main post-migration smoke test, checking `raw`, `public`, and `rpt` explicitly. |
| `scripts/summarise_v4_scope_control_scores.py` | Active analysis utility, not a PostgreSQL dependency | Uses a CSV dataclass field named `source_file`, but reads manual-score CSV files directly from `docs/failure_audits`. | Terminology collision only. It should not block PostgreSQL schema movement. No database-object dependency was found in this file. |
| `scripts/export_dataset_to_duckdb.py` | Active/auxiliary, separate database | Creates a DuckDB table also named `source_file` from JSONL datasets. | Separate DuckDB schema, not a PostgreSQL dependency. Mention only to avoid confusing plain-text searches for `source_file`. |

## Views

| View | Current location | Target location | Current readers | Migration note |
|---|---|---|---|---|
| `case_run_trace` | `public` | `rpt` | `scripts/export_case_card.py`, `scripts/test_postgres_provenance_layer.ps1`, docs, generated case cards, ad hoc SQL examples. | Move only after adding/updating readers. A temporary `public.case_run_trace AS SELECT * FROM rpt.case_run_trace` alias is justified. |
| `case_run_trace_reporting` | `public` | `rpt` | health-check script, docs, ad hoc reporting queries. | Same as above. It should keep the current reporting filters unless deliberately revised in a separate reporting cleanup. |
| `score_linkage_status` | `public` | `rpt` | docs and manual score linkage diagnostics. | Move to `rpt`; optionally keep public alias during transition. It is diagnostic and must not weaken `score_event` response-level semantics. |

## Documentation and generated artefacts

| File | Classification | Current references | Schema-separation impact |
|---|---:|---|---|
| `docs/postgres_provenance_layer.md` | Documentation / active commands | Documents initialisation through `001`, ingest source plan, rebuild commands, unqualified query examples over `dataset_case`, `dataset`, `source_file`, `case_run_trace`, `score_linkage_status`, and case-card export. | Must be updated after scripts/views are migrated. Until then it correctly describes the current public-schema workflow. |
| `docs/design/sql_migration_inventory.md` | Documentation / design | Classifies current `sql/*.sql` files and already recommends a future bootstrap plus legacy migration folder. Notes the missing `moral_domain` definition. | Should be updated after schema separation design is accepted. It should remain conceptually separate from this dependency inventory. |
| `docs/diagrams/postgres_operational_physical_er_model.md` | Documentation / design | States that operational tables remain in `public` for now; import-shaped tables are included for lineage/FK anchoring; notes raw/public/rpt as a likely future split. | Needs an updated diagram once the split is implemented. No immediate code impact. |
| `docs/diagrams/postgres_operational_relationships.mmd` | Documentation / diagram source | Shows relationships among import, operational, and score tables. | Needs schema-qualified labelling or a new diagram after the split. |
| `docs/case_cards/scope_control_military_strong_authority_urgency.md` | Generated/curated documentation | States that the card was exported from `case_run_trace`. | Historical/generated artefact. Does not need to change immediately, but future generated cards should say `rpt.case_run_trace` once the reporting view moves. |

## What would break if raw tables moved immediately

1. `scripts/ingest_eval_artifacts_to_postgres.py` would fail on unqualified inserts/selects into `source_file`, `dataset`, `dataset_case`, `case_intervention`, `expected_behaviour`, `model_run`, `model_response`, `structured_decision_tuple`, `manual_score`, and `deterministic_score`.

2. `--init-schema` would still execute `sql/001_create_eval_provenance_schema.sql`, recreating the old public-schema import tables and public views rather than the intended split.

3. `--reset-data` would either fail or truncate the wrong objects. More importantly, it currently truncates import/provenance tables but not all derived operational tables, so stale `eval_case`, `case_turn`, `response`, and `score_event` rows are a risk if reset semantics are not redesigned.

4. Rebuild/backfill SQL files `003`, `004`, `005`, `009`, `011`, `012`, `013`, and `014` would fail or read stale compatibility objects unless all raw references were schema-qualified or explicitly aliased.

5. `case_run_trace` would fail unless recreated against the new raw/public object locations. `case_run_trace_reporting` would fail because it depends on `case_run_trace`.

6. `scripts/export_case_card.py` would fail unless it is updated to `rpt.case_run_trace` or a temporary public alias exists.

7. `scripts/test_postgres_provenance_layer.ps1` would fail its public-view existence checks and raw-table count queries.

8. `scripts/diagnose_score_linkage.py` would fail on unqualified reads of raw/import score and response tables.

9. Documentation commands in `docs/postgres_provenance_layer.md` would become misleading or wrong.

10. Foreign-key assumptions need review. Operational tables currently point back to import-shaped tables for lineage, for example `eval_case.dataset_case_pk`, `case_turn.source_case_pk`, `case_turn.source_intervention_id`, `response.legacy_model_response_id`, and `score_event.legacy_manual_score_id` / `legacy_deterministic_score_id`. These relationships should be preserved deliberately, not accidentally broken by a table move.

## Ordered migration plan

### 1. Freeze the current provenance layer as the baseline

Do not run model evals. Do not mutate datasets. Do not move tables. Do not create `bootstrap_current_schema.sql` yet.

Use the current database and generated health checks only as a baseline for row counts, object existence, and reporting-view behaviour.

### 2. Decide the schema contract before writing SQL

Confirm these object-location decisions:

- `raw.model_run` versus `public.model_run`. Recommendation: `raw.model_run`, because it is imported run metadata, with public operational/reporting joins explicitly reaching into `raw`.
- `public.failure_class` and `public.rubric`. Recommendation: public operational tables only. Do not keep raw duplicates unless a future importer needs raw audit labels separately.
- `raw.source_file`. Recommendation: raw, because it is source-ingest metadata, but it will be widely referenced from public/rpt for provenance.
- `rpt.case_run_trace`, `rpt.case_run_trace_reporting`, `rpt.score_linkage_status`. Recommendation: move reporting views to `rpt`, with temporary public aliases.

### 3. Add schema-awareness to active scripts before moving tables

Update active scripts to tolerate explicit schema names, preferably through small constants or configuration defaults:

- raw schema default: `raw`
- operational schema default: `public`
- reporting schema default: `rpt`

Do this before moving objects. During this phase the defaults can still point at current public objects or use compatibility views. The point is to remove hard-coded assumptions gradually rather than playing SQL whack-a-mole, which is a sport with no spectators and fewer winners.

Priority order:

1. `scripts/ingest_eval_artifacts_to_postgres.py`
2. `scripts/export_case_card.py`
3. `scripts/diagnose_score_linkage.py`
4. `scripts/test_postgres_provenance_layer.ps1`

### 4. Split reset semantics explicitly

Before moving tables, decide whether reset means:

- raw-only reset: truncate/re-ingest raw imported artefacts, then require an explicit post-ingest operational rebuild; or
- full rebuild reset: truncate raw and derived operational/reporting-dependent rows in a safe dependency order.

Recommendation: implement two explicit modes later:

- `--reset-raw --yes`
- `--reset-derived --yes` or `--full-rebuild --yes`

The current `--reset-data` name is too vague for a split schema. It worked while the provenance layer was small; it is now a footgun with a polite CLI flag.

### 5. Create reporting views in `rpt` before changing active readers

When SQL changes are allowed, create:

- `rpt.case_run_trace`
- `rpt.case_run_trace_reporting`
- `rpt.score_linkage_status`

Then add temporary compatibility aliases:

- `public.case_run_trace AS SELECT * FROM rpt.case_run_trace`
- `public.case_run_trace_reporting AS SELECT * FROM rpt.case_run_trace_reporting`
- `public.score_linkage_status AS SELECT * FROM rpt.score_linkage_status`

These aliases are justified because active scripts and docs currently read the public names. Keep them transitional and documented.

### 6. Schema-qualify the current rebuild/backfill path

The current recommended rebuild path is:

- `001`
- `002`
- `003`
- `004`
- `005`
- `009`
- `011`
- `012`
- `013`
- `014`

Before any table move, rewrite or replace the active parts of this path so raw reads/writes use `raw.*`, operational objects use `public.*`, and reporting views use `rpt.*`.

Do not invest effort in preserving public-name compatibility for historical migrations `006`, `007`, `008`, and `010`. Move them to a legacy folder after the future bootstrap is validated.

### 7. Move or rebuild raw/import tables only after readers and writers are ready

Once active scripts and current rebuild SQL are schema-aware, move or rebuild the import tables into `raw`.

Use one of two approaches:

- Controlled `ALTER TABLE ... SET SCHEMA raw` migration, with views recreated afterwards; or
- clean rebuild into `raw` from files, followed by public operational backfill and rpt view recreation.

Recommendation: prefer clean rebuild for this project if row counts and source-file hashes are easy to verify. The database is an index/provenance layer over repository artefacts, not the source of truth.

### 8. Validate using counts and invariants, not model reruns

Validation should compare old and new database states using:

- source-file count and hash inventory;
- dataset/case counts by dataset version;
- non-blank legacy `model_response` rows versus operational `response` rows;
- `manual_score` and `deterministic_score` linkage counts;
- `case_run_trace` versus `case_run_trace_reporting` row counts;
- reporting guard checks for smoke, summary, expansion-candidate, and rewrite-candidate artefacts;
- a case-card export from a known case.

No model evals are needed for this.

### 9. Update docs and diagrams after the split is working

Update:

- `docs/postgres_provenance_layer.md`
- `docs/design/sql_migration_inventory.md`
- `docs/diagrams/postgres_operational_physical_er_model.md`
- `docs/diagrams/postgres_operational_relationships.mmd`
- any case-card generation note that refers to `case_run_trace`

Do not update documentation first and leave the implementation behind it. That is how archaeology happens.

### 10. Create `bootstrap_current_schema.sql` only after the migration path is stable

The future bootstrap should create the end-state directly:

- `raw` import/source tables;
- `public` operational tables and lookups;
- `rpt` reporting/diagnostic views;
- indexes, constraints, triggers, and seed rows;
- explicit current timestamp conventions;
- `moral_domain` table definition;
- final `rubric` and `failure_class` definitions;
- no historical repair detritus.

Data-population logic that depends on ingested artefact rows should probably live in a separate, explicit post-ingest script such as `bootstrap_after_ingest.sql` rather than being mixed into pure DDL.

## Compatibility view policy

Use compatibility views only for active entry points that would otherwise break immediately:

- public aliases for `rpt` views are justified during transition;
- raw-table public aliases may be justified briefly for active scripts if code updates cannot land atomically;
- do not preserve aliases for superseded historical migrations;
- document every alias with a removal condition.

A compatibility layer without a removal plan becomes the new schema. That is not a transition; it is clutter with tenure.

## Recommended next step

Do not move tables yet. The next safe implementation task is to prepare a non-mutating patch plan for active scripts and SQL references, starting with schema-qualified names and reset semantics. Only after that should schema-creation/move SQL be written.
