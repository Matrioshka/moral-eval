# SQL Migration Inventory

Date: 2026-06-08

This inventory reviews the current `sql/*.sql` files and recommends how each
should be treated before any future cleanup. It does not move or edit SQL files.

## Summary

The SQL directory currently mixes four concerns:

- a base provenance/import schema;
- additive operational normalisation migrations;
- repair/backfill migrations created while the operational layer matured;
- read-only reporting/diagnostic views.

For a future clean rebuild, the current schema should be consolidated into a
single `bootstrap_current_schema.sql` that creates the desired end-state tables,
indexes, triggers, lookup rows, and views directly. Historical repair scripts
should then move to `sql/legacy_migrations/` for auditability.

One important gap: `002_populate_moral_domain_descriptions.sql` assumes a
`moral_domain` table exists, but no current `sql/*.sql` file creates it. A future
bootstrap should define `moral_domain` before seeding descriptions.

## Inventory

| File | Classification | Purpose | Recommendation |
|---|---|---|---|
| `001_create_eval_provenance_schema.sql` | Required for fresh rebuild | Creates the legacy/import provenance tables, legacy score tables, and `case_run_trace` reporting views. | Remain for now. Fold into `bootstrap_current_schema.sql`, updating it to include current end-state columns such as rubric timestamps and any missing lookup table definitions. |
| `002_populate_moral_domain_descriptions.sql` | Required for fresh rebuild | Seeds and updates moral-domain descriptions. | Remain for now, but only after adding/confirming the `moral_domain` table definition. Fold the lookup table and seed data into future bootstrap. |
| `003_create_and_populate_scenario.sql` | Required for fresh rebuild | Creates `scenario`, populates it from `dataset_case.scenario`, and links `dataset_case.scenario_id`. | Remain for now. Fold into future bootstrap as current schema plus post-ingest normalisation/backfill logic. |
| `004_update_scenario_names.sql` | Operational migration | Improves `scenario.name` from canonical case metadata for browsing, diagrams, and exports. | Remain for now if scenario names are still derived post-ingest. Fold into bootstrap or into a documented post-ingest normalisation step. |
| `005_create_operational_case_turn_model.sql` | Operational migration | Adds the transitional operational shape: `eval_case`, `case_turn`, `response`, and `evidence_quality`; backfills from legacy provenance rows. | Remain for now. Fold the table definitions into future bootstrap and keep the data-population parts as a post-ingest backfill step. |
| `006_create_turn_pressure_failure_lookups.sql` | Historical/superseded | Creates early `turn_type`, `pressure_type`, and `failure_class` lookups. Later normalisation/repair scripts defensively recreate and refine these tables. | Move to `sql/legacy_migrations/` after bootstrap exists. Fold the final lookup definitions and seed data into bootstrap, not this early version. |
| `007_normalise_turns_and_scores.sql` | Historical/superseded | First attempt to normalise turn metadata and create/populate `score_event`. | Move to `sql/legacy_migrations/`. Superseded by `008`/`009`; fold only the final `score_event`, scorer, lookup, and FK shape into bootstrap. |
| `008_repair_normalise_turns_and_scores.sql` | Historical/superseded repair migration | Repairs the early `007` pressure-type conflict and completes score normalisation. | Move to `sql/legacy_migrations/`. Superseded by `009`; preserve for audit history only. |
| `009_fix_turn_score_normalisation.sql` | Repair migration | Fixes and completes turn/score normalisation after `008` `score_event` conflict; creates robust scorer, lookup, and `score_event` structures. | Remain for now. Fold the final schema objects and seed rows into future bootstrap; move the historical repair script to legacy after bootstrap is validated. |
| `010_backfill_all_operational_responses_and_scores.sql` | Historical/superseded repair migration | Attempts broader operational response and score-event backfill from legacy rows. | Move to `sql/legacy_migrations/`. Explicitly superseded by `011_backfill_response_score_coverage_deduped.sql`. |
| `011_backfill_response_score_coverage_deduped.sql` | Repair migration | Deduplicates operational case sources and backfills operational responses plus score-event coverage from legacy rows. | Remain for now. Fold the safe deduplicated response/score coverage logic into a future post-ingest backfill or bootstrap companion. |
| `012_add_rubric_timestamps_for_score_backfill.sql` | Repair migration | Adds `created_at`/`updated_at` and an update trigger to `rubric` for compatibility with later score backfills. | Remain for now. Fold directly into the bootstrap `rubric` definition; once folded, move this compatibility patch to legacy. |
| `013_backfill_unresolved_legacy_response_turns.sql` | Repair migration | Adds `unresolved_legacy_prompt` and backfills remaining valid legacy responses that lacked reconstructable case turns, then broadens score-event coverage. | Remain for now. Fold the placeholder turn type and remaining-response handling into future bootstrap/post-ingest backfill; review manual-score ambiguity before preserving broad score-event import behaviour. |
| `014_create_score_linkage_status_view.sql` | Diagnostic/reporting view | Creates read-only `score_linkage_status` view for manual-score linkage coverage and ambiguity inspection. | Remain for now for existing/public-layout rebuilds. Superseded for split-schema reporting by `015_create_rpt_reporting_views.sql`; fold the diagnostic view into future bootstrap under `rpt`. |
| `015_create_rpt_reporting_views.sql` | Active reporting migration | Creates `rpt.case_run_trace`, `rpt.case_run_trace_reporting`, and `rpt.score_linkage_status`, while retaining public compatibility views that delegate to `rpt`. Does not move raw/import tables. | Remain. This is the first actual schema-separation migration. Fold final reporting-view definitions into future bootstrap. |

## Recommended Current Layout

Until a tested `bootstrap_current_schema.sql` exists, keep the current numbered
files in place except for clearly superseded scripts that can be moved after a
separate cleanup task:

- Keep current operational/fresh rebuild path: `001`, `002`, `003`, `004`, `005`,
  `009`, `011`, `012`, `013`, `014`, `015`.
- Candidate legacy moves after validation: `006`, `007`, `008`, `010`.
- Do not add new data-mutating migrations for score linkage until the diagnostic
  evidence supports specific, reviewed, unambiguous links.

## Future Bootstrap Contents

A future `bootstrap_current_schema.sql` should create the current end-state
directly:

- legacy provenance/import tables from `001`;
- missing/current lookup definitions, including `moral_domain`;
- `scenario`, `evidence_quality`, `turn_type`, `pressure_type`,
  `failure_class`, `scorer`, and `rubric` with final timestamp conventions;
- operational tables `eval_case`, `case_turn`, `response`, and `score_event`;
- current indexes, constraints, and `set_updated_at` triggers;
- reporting views `rpt.case_run_trace`, `rpt.case_run_trace_reporting`, and
  `rpt.score_linkage_status`, plus any deliberate public compatibility aliases
  still needed at bootstrap time.

Data-population logic that depends on ingested artefact rows should either remain
as a clearly named post-ingest backfill script or be split into an explicit
`bootstrap_after_ingest.sql`.
