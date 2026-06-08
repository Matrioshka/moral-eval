-- Recreate reporting and diagnostic views against raw import tables.
--
-- Expected prior migration:
--   sql/017_move_import_tables_to_raw.sql
--
-- This migration keeps public compatibility views for reporting, but does not
-- create public aliases for raw/import tables.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpt;

DROP VIEW IF EXISTS public.case_run_trace_reporting;
DROP VIEW IF EXISTS public.case_run_trace;
DROP VIEW IF EXISTS public.score_linkage_status;

DROP VIEW IF EXISTS rpt.case_run_trace_reporting;
DROP VIEW IF EXISTS rpt.case_run_trace;
DROP VIEW IF EXISTS rpt.score_linkage_status;

CREATE VIEW rpt.case_run_trace AS
SELECT
    dc.case_pk,
    dc.sample_id,
    dc.case_id,
    dc.source_item_id,
    d.dataset_version,
    d.dataset_family,
    CASE
        WHEN sf_case.file_kind = 'dataset_jsonl' THEN 'dataset_jsonl'
        WHEN dc.case_origin IS NOT NULL AND dc.case_origin <> 'unknown' THEN dc.case_origin
        WHEN dc.source_file_id IS NULL THEN 'csv_inferred_or_unlinked'
        ELSE 'csv_inferred'
    END AS case_origin,
    (sf_case.file_kind = 'dataset_jsonl') AS is_canonical_dataset_item,
    dc.variant,
    COALESCE(mr.prompt_style, dc.prompt_style, mresp.prompt_style) AS prompt_style,
    dc.moral_domain,
    dc.risk_track,
    dc.scenario,
    dc.initial_judgement,
    ci.user_followup,
    CASE
        WHEN jsonb_typeof(dc.raw_record -> 'pressure_turns') = 'array' THEN dc.raw_record -> 'pressure_turns'
        WHEN jsonb_typeof(mresp.raw_row -> 'pressure_turns') = 'array' THEN mresp.raw_row -> 'pressure_turns'
        ELSE NULL::jsonb
    END AS pressure_turns,
    CASE
        WHEN (mresp.raw_row ->> 'pressure_turn_count') ~ '^[0-9]+$'
            THEN (mresp.raw_row ->> 'pressure_turn_count')::integer
        WHEN jsonb_typeof(dc.raw_record -> 'pressure_turns') = 'array'
            THEN jsonb_array_length(dc.raw_record -> 'pressure_turns')
        WHEN jsonb_typeof(mresp.raw_row -> 'pressure_turns') = 'array'
            THEN jsonb_array_length(mresp.raw_row -> 'pressure_turns')
        ELSE NULL::integer
    END AS pressure_turn_count,
    ci.evidence_quality,
    ci.pressure_type,
    ci.followup_strength,
    ci.expected_update,
    ci.pressure_source,
    ci.pressure_mechanism,
    ci.pressure_legitimacy,
    ci.pressure_target,
    ci.conflict_type,
    eb.expected_behaviour,
    eb.ideal_behaviour,
    eb.target_scope,
    eb.minimum_acceptable_scope,
    eb.maximum_acceptable_scope,
    eb.target_release_scope,
    eb.minimum_acceptable_release_scope,
    eb.maximum_acceptable_release_scope,
    eb.target_access,
    eb.minimum_acceptable_access,
    eb.maximum_acceptable_access,
    eb.release_artifact,
    eb.missing_evaluation_types,
    ci.safeguard_type,
    ci.safeguard_features,
    eb.failure_modes,
    ci.residual_risk_features,
    mr.run_id,
    mr.run_label,
    mr.model_name,
    mr.provider,
    mr.run_timestamp,
    mresp.response_id,
    mresp.raw_response AS model_raw_response,
    sdt.tuple_schema,
    sdt.raw_tuple AS extracted_structured_tuple,
    sdt.access_purpose AS extracted_access_purpose,
    sdt.access_intent AS extracted_access_intent,
    sdt.access_population AS extracted_access_population,
    sdt.access_modality AS extracted_access_modality,
    sdt.operational_status AS extracted_operational_status,
    sdt.real_world_exposure AS extracted_real_world_exposure,
    sdt.externalisation_level AS extracted_externalisation_level,
    sdt.legacy_release_scope AS extracted_legacy_release_scope,
    ms.score_0_to_3 AS manual_score,
    fc.name AS failure_class,
    ms.confidence AS manual_confidence,
    ms.action AS manual_action,
    ms.notes AS grading_rationale,
    ds.scorer_name AS deterministic_scorer,
    ds.score_value AS deterministic_score,
    ds.answer AS deterministic_answer,
    ds.explanation AS deterministic_explanation,
    ds.metadata AS deterministic_metadata,
    jsonb_strip_nulls(jsonb_build_object(
        'dataset_case', sf_case.file_path,
        'dataset_case_kind', sf_case.file_kind,
        'model_response', sf_response.file_path,
        'manual_score', sf_manual.file_path,
        'deterministic_score', sf_deterministic.file_path,
        'dataset_source_sha256', sf_case.content_sha256,
        'response_source_sha256', sf_response.content_sha256,
        'manual_source_sha256', sf_manual.content_sha256
    )) AS source_files
FROM raw.dataset_case dc
JOIN raw.dataset d ON d.dataset_id = dc.dataset_id
LEFT JOIN raw.case_intervention ci ON ci.case_pk = dc.case_pk
LEFT JOIN raw.expected_behaviour eb ON eb.case_pk = dc.case_pk
LEFT JOIN raw.model_response mresp ON mresp.case_pk = dc.case_pk
LEFT JOIN raw.model_run mr ON mr.run_id = mresp.run_id
LEFT JOIN raw.structured_decision_tuple sdt ON sdt.response_id = mresp.response_id
LEFT JOIN raw.manual_score ms ON ms.response_id = mresp.response_id
LEFT JOIN public.failure_class fc ON fc.failure_class_id = ms.primary_failure_class_id
LEFT JOIN raw.deterministic_score ds ON ds.response_id = mresp.response_id
LEFT JOIN raw.source_file sf_case ON sf_case.source_file_id = dc.source_file_id
LEFT JOIN raw.source_file sf_response ON sf_response.source_file_id = mresp.source_file_id
LEFT JOIN raw.source_file sf_manual ON sf_manual.source_file_id = ms.source_file_id
LEFT JOIN raw.source_file sf_deterministic ON sf_deterministic.source_file_id = ds.source_file_id;

COMMENT ON VIEW rpt.case_run_trace IS
    'Reporting trace view over raw provenance tables and public operational lookups.';

CREATE VIEW rpt.case_run_trace_reporting AS
SELECT *
FROM rpt.case_run_trace
WHERE is_canonical_dataset_item IS TRUE
  AND response_id IS NOT NULL
  AND COALESCE(run_label, '') !~* '(smoke|summary)'
  AND COALESCE(dataset_version, '') !~* '(expansion_candidates|rewrite_candidate)'
  AND COALESCE(source_files::text, '') !~* '(smoke|summary|expansion_candidates|rewrite_candidate)';

COMMENT ON VIEW rpt.case_run_trace_reporting IS
    'Reporting-safe subset of rpt.case_run_trace, excluding smoke, summary, expansion-candidate, and rewrite-candidate artefacts.';

CREATE VIEW rpt.score_linkage_status AS
SELECT
    ms.manual_score_id,
    ms.response_id AS legacy_model_response_id,
    se.score_event_id,
    se.response_id AS operational_response_id,
    (se.score_event_id IS NOT NULL) AS is_linked_to_score_event,
    CASE
        WHEN se.score_event_id IS NOT NULL THEN 'linked_to_score_event'
        WHEN mr.response_id IS NULL THEN 'unlinked_missing_legacy_model_response'
        WHEN mr.raw_response IS NULL OR length(trim(mr.raw_response)) = 0 THEN 'unlinked_blank_legacy_response_stub'
        WHEN r.response_id IS NULL THEN 'unlinked_valid_legacy_response_missing_operational_response'
        ELSE 'unlinked_other'
    END AS linkage_status,
    CASE
        WHEN mr.raw_response IS NOT NULL AND length(trim(mr.raw_response)) > 0 THEN true
        ELSE false
    END AS legacy_model_response_has_text,
    CASE
        WHEN r.response_id IS NOT NULL THEN true
        ELSE false
    END AS has_operational_response,
    mr.case_pk,
    mr.sample_id,
    mr.case_id,
    mr.source_item_id,
    mr.dataset_version,
    mr.prompt_style AS response_prompt_style,
    run.run_id,
    run.run_label,
    run.model_name,
    run.prompt_style AS run_prompt_style,
    ms.score_0_to_3 AS manual_score,
    fc.name AS failure_class,
    ms.confidence AS manual_confidence,
    ms.action AS manual_action,
    ms.notes AS manual_notes,
    sf_manual.file_path AS manual_score_source_path,
    ms.source_row AS manual_score_source_row,
    sf_response.file_path AS legacy_response_source_path,
    mr.source_row AS legacy_response_source_row,
    sf_operational.file_path AS operational_response_source_path,
    r.source_row AS operational_response_source_row,
    ms.created_at AS manual_score_created_at,
    se.created_at AS score_event_created_at
FROM raw.manual_score ms
LEFT JOIN raw.model_response mr
    ON mr.response_id = ms.response_id
LEFT JOIN raw.model_run run
    ON run.run_id = mr.run_id
LEFT JOIN public.response r
    ON r.legacy_model_response_id = mr.response_id
LEFT JOIN public.score_event se
    ON se.legacy_manual_score_id = ms.manual_score_id
LEFT JOIN public.failure_class fc
    ON fc.failure_class_id = ms.primary_failure_class_id
LEFT JOIN raw.source_file sf_manual
    ON sf_manual.source_file_id = ms.source_file_id
LEFT JOIN raw.source_file sf_response
    ON sf_response.source_file_id = mr.source_file_id
LEFT JOIN raw.source_file sf_operational
    ON sf_operational.source_file_id = r.source_file_id;

COMMENT ON VIEW rpt.score_linkage_status IS
    'Read-only diagnostic view showing whether each raw.manual_score row is linked to a response-level score_event, and why unlinked rows remain outside score_event.';

CREATE VIEW public.case_run_trace AS
SELECT * FROM rpt.case_run_trace;

COMMENT ON VIEW public.case_run_trace IS
    'Compatibility view. Prefer rpt.case_run_trace for new reporting queries.';

CREATE VIEW public.case_run_trace_reporting AS
SELECT * FROM rpt.case_run_trace_reporting;

COMMENT ON VIEW public.case_run_trace_reporting IS
    'Compatibility view. Prefer rpt.case_run_trace_reporting for new reporting queries.';

CREATE VIEW public.score_linkage_status AS
SELECT * FROM rpt.score_linkage_status;

COMMENT ON VIEW public.score_linkage_status IS
    'Compatibility view. Prefer rpt.score_linkage_status for new diagnostic queries.';

COMMIT;
