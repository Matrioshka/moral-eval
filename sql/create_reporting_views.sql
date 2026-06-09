-- Recreate reporting and diagnostic views over public operational metadata
-- and raw provenance rows.
--
-- Expected prior steps:
--   raw import tables exist and are populated.
--   public dataset/run dimensions have been promoted from raw.
--   public operational response/case tables have been backfilled from raw.
--   public case expectations and structured decisions have been promoted from raw.
--
-- This migration keeps public compatibility views for reporting, but does not
-- create public aliases for raw/import tables.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpt;

DROP VIEW IF EXISTS public.run_detail;
DROP VIEW IF EXISTS public.run_summary;
DROP VIEW IF EXISTS public.case_run_trace_reporting;
DROP VIEW IF EXISTS public.case_run_trace;
DROP VIEW IF EXISTS public.score_linkage_status;

DROP VIEW IF EXISTS rpt.run_detail;
DROP VIEW IF EXISTS rpt.run_summary;
DROP VIEW IF EXISTS rpt.case_run_trace_reporting;
DROP VIEW IF EXISTS rpt.case_run_trace;
DROP VIEW IF EXISTS rpt.score_linkage_status;

CREATE VIEW rpt.run_summary AS
WITH response_summary AS (
    SELECT
        resp.run_id,
        count(DISTINCT resp.response_id) AS response_count,
        count(DISTINCT ec.eval_case_id) AS case_count,
        count(DISTINCT ct.case_turn_id) AS case_turn_count,
        count(DISTINCT rsd.response_structured_decision_id) AS structured_decision_count,
        min(resp.created_at) AS first_response_created_at,
        max(resp.created_at) AS latest_response_created_at
    FROM public.response resp
    LEFT JOIN public.case_turn ct ON ct.case_turn_id = resp.case_turn_id
    LEFT JOIN public.eval_case ec ON ec.eval_case_id = ct.eval_case_id
    LEFT JOIN public.response_structured_decision rsd ON rsd.response_id = resp.response_id
    GROUP BY resp.run_id
), score_summary AS (
    SELECT
        resp.run_id,
        count(DISTINCT se.response_id) AS scored_response_count,
        count(se.score_event_id) AS score_event_count,
        count(se.score_event_id) FILTER (WHERE scorer.scorer_type = 'imported_manual_audit') AS manual_score_count,
        count(se.score_event_id) FILTER (WHERE scorer.scorer_type = 'deterministic') AS deterministic_score_count
    FROM public.response resp
    JOIN public.score_event se ON se.response_id = resp.response_id
    LEFT JOIN public.scorer scorer ON scorer.scorer_id = se.scorer_id
    GROUP BY resp.run_id
)
SELECT
    rn.run_id,
    rn.run_label,
    rn.model_name,
    rn.provider,
    rn.dataset_id,
    COALESCE(d.dataset_version, rn.dataset_version) AS dataset_version,
    d.dataset_family,
    rn.prompt_style,
    rn.run_timestamp,
    rn.source_file_id,
    COALESCE(response_summary.response_count, 0)::bigint AS response_count,
    COALESCE(response_summary.case_count, 0)::bigint AS case_count,
    COALESCE(response_summary.case_turn_count, 0)::bigint AS case_turn_count,
    COALESCE(response_summary.structured_decision_count, 0)::bigint AS structured_decision_count,
    COALESCE(score_summary.scored_response_count, 0)::bigint AS scored_response_count,
    COALESCE(score_summary.score_event_count, 0)::bigint AS score_event_count,
    COALESCE(score_summary.manual_score_count, 0)::bigint AS manual_score_count,
    COALESCE(score_summary.deterministic_score_count, 0)::bigint AS deterministic_score_count,
    response_summary.first_response_created_at,
    response_summary.latest_response_created_at,
    rn.created_at AS run_created_at,
    rn.updated_at AS run_updated_at
FROM public.run rn
LEFT JOIN public.dataset d ON d.dataset_id = rn.dataset_id
LEFT JOIN response_summary ON response_summary.run_id = rn.run_id
LEFT JOIN score_summary ON score_summary.run_id = rn.run_id;

COMMENT ON VIEW rpt.run_summary IS
    'One row per public.run with response, case, structured-decision, and score counts for run list pages.';

CREATE VIEW rpt.run_detail AS
WITH score_rollup AS (
    SELECT
        se.response_id,
        count(se.score_event_id) AS score_event_count,
        count(se.score_event_id) FILTER (WHERE scorer.scorer_type = 'imported_manual_audit') AS manual_score_count,
        count(se.score_event_id) FILTER (WHERE scorer.scorer_type = 'deterministic') AS deterministic_score_count,
        max(se.score) FILTER (WHERE scorer.scorer_type = 'imported_manual_audit') AS manual_score,
        max(fc.name) FILTER (WHERE scorer.scorer_type = 'imported_manual_audit') AS failure_class,
        max(se.confidence_label) FILTER (WHERE scorer.scorer_type = 'imported_manual_audit') AS manual_confidence,
        max(se.label) FILTER (WHERE scorer.scorer_type = 'imported_manual_audit') AS manual_action,
        max(se.rationale) FILTER (WHERE scorer.scorer_type = 'imported_manual_audit') AS grading_rationale,
        COALESCE(
            jsonb_agg(
                jsonb_strip_nulls(jsonb_build_object(
                    'score_event_id', se.score_event_id,
                    'scorer_type', scorer.scorer_type,
                    'scorer_name', scorer.name,
                    'score', se.score,
                    'label', se.label,
                    'failure_class', fc.name,
                    'confidence', se.confidence,
                    'confidence_label', se.confidence_label,
                    'rationale', se.rationale
                ))
                ORDER BY se.score_event_id
            ) FILTER (WHERE se.score_event_id IS NOT NULL),
            '[]'::jsonb
        ) AS score_events
    FROM public.score_event se
    LEFT JOIN public.scorer scorer ON scorer.scorer_id = se.scorer_id
    LEFT JOIN public.failure_class fc ON fc.failure_class_id = se.failure_class_id
    GROUP BY se.response_id
)
SELECT
    rn.run_id,
    rn.run_label,
    rn.model_name,
    rn.provider,
    rn.dataset_id,
    COALESCE(d.dataset_version, rn.dataset_version) AS dataset_version,
    d.dataset_family,
    rn.prompt_style AS run_prompt_style,
    rn.run_timestamp,
    ec.eval_case_id,
    ec.dataset_case_pk AS case_pk,
    ec.sample_id,
    ec.case_id,
    ec.source_item_id,
    scenario.scenario_id,
    scenario.name AS scenario_name,
    scenario.scenario_text AS scenario,
    ce.expected_behaviour,
    ce.ideal_behaviour,
    ce.target_scope,
    ce.minimum_acceptable_scope,
    ce.maximum_acceptable_scope,
    ce.target_release_scope,
    ce.minimum_acceptable_release_scope,
    ce.maximum_acceptable_release_scope,
    ce.target_access,
    ce.minimum_acceptable_access,
    ce.maximum_acceptable_access,
    ce.release_artifact,
    ce.missing_evaluation_types,
    ce.failure_modes,
    ct.case_turn_id,
    ct.parent_turn_id,
    ct.turn_index,
    ct.turn_type,
    tt.slug AS turn_type_slug,
    ct.turn_text,
    ct.intervention_role,
    ct.pressure_type,
    pt.slug AS pressure_type_slug,
    eq.slug AS evidence_quality_slug,
    eq.name AS evidence_quality_name,
    resp.response_id,
    resp.legacy_model_response_id,
    resp.response_role,
    resp.response_text AS model_raw_response,
    rsd.tuple_schema,
    rsd.raw_tuple AS extracted_structured_tuple,
    rsd.access_intent AS extracted_access_intent,
    rsd.access_population AS extracted_access_population,
    rsd.access_modality AS extracted_access_modality,
    rsd.operational_status AS extracted_operational_status,
    rsd.real_world_exposure AS extracted_real_world_exposure,
    rsd.externalisation_level AS extracted_externalisation_level,
    COALESCE(score_rollup.score_event_count, 0)::bigint AS score_event_count,
    COALESCE(score_rollup.manual_score_count, 0)::bigint AS manual_score_count,
    COALESCE(score_rollup.deterministic_score_count, 0)::bigint AS deterministic_score_count,
    score_rollup.manual_score,
    score_rollup.failure_class,
    score_rollup.manual_confidence,
    score_rollup.manual_action,
    score_rollup.grading_rationale,
    COALESCE(score_rollup.score_events, '[]'::jsonb) AS score_events,
    resp.source_file_id AS response_source_file_id,
    resp.source_row AS response_source_row,
    resp.created_at AS response_created_at,
    resp.updated_at AS response_updated_at
FROM public.run rn
LEFT JOIN public.dataset d ON d.dataset_id = rn.dataset_id
JOIN public.response resp ON resp.run_id = rn.run_id
LEFT JOIN public.case_turn ct ON ct.case_turn_id = resp.case_turn_id
LEFT JOIN public.eval_case ec ON ec.eval_case_id = ct.eval_case_id
LEFT JOIN public.scenario scenario ON scenario.scenario_id = ec.scenario_id
LEFT JOIN public.case_expectation ce ON ce.eval_case_id = ec.eval_case_id
LEFT JOIN public.response_structured_decision rsd ON rsd.response_id = resp.response_id
LEFT JOIN public.turn_type tt ON tt.turn_type_id = ct.turn_type_id
LEFT JOIN public.pressure_type pt ON pt.pressure_type_id = ct.pressure_type_id
LEFT JOIN public.evidence_quality eq ON eq.evidence_quality_id = ct.evidence_quality_id
LEFT JOIN score_rollup ON score_rollup.response_id = resp.response_id;

COMMENT ON VIEW rpt.run_detail IS
    'One row per response within a public.run, with case, turn, expectation, structured decision, and score rollup fields.';

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
    COALESCE(run.prompt_style, dc.prompt_style, mresp.prompt_style) AS prompt_style,
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
    ce.expected_behaviour,
    ce.ideal_behaviour,
    ce.target_scope,
    ce.minimum_acceptable_scope,
    ce.maximum_acceptable_scope,
    ce.target_release_scope,
    ce.minimum_acceptable_release_scope,
    ce.maximum_acceptable_release_scope,
    ce.target_access,
    ce.minimum_acceptable_access,
    ce.maximum_acceptable_access,
    ce.release_artifact,
    ce.missing_evaluation_types,
    ci.safeguard_type,
    ci.safeguard_features,
    ce.failure_modes,
    ci.residual_risk_features,
    run.run_id,
    run.run_label,
    run.model_name,
    run.provider,
    run.run_timestamp,
    mresp.response_id,
    mresp.raw_response AS model_raw_response,
    rsd.tuple_schema,
    rsd.raw_tuple AS extracted_structured_tuple,
    rsd.access_intent AS extracted_access_intent,
    rsd.access_population AS extracted_access_population,
    rsd.access_modality AS extracted_access_modality,
    rsd.operational_status AS extracted_operational_status,
    rsd.real_world_exposure AS extracted_real_world_exposure,
    rsd.externalisation_level AS extracted_externalisation_level,
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
        'case_expectation', sf_expectation.file_path,
        'model_response', sf_response.file_path,
        'structured_decision', sf_structured.file_path,
        'manual_score', sf_manual.file_path,
        'deterministic_score', sf_deterministic.file_path,
        'dataset_source_sha256', sf_case.content_sha256,
        'expectation_source_sha256', sf_expectation.content_sha256,
        'response_source_sha256', sf_response.content_sha256,
        'structured_decision_source_sha256', sf_structured.content_sha256,
        'manual_source_sha256', sf_manual.content_sha256
    )) AS source_files
FROM raw.dataset_case dc
JOIN public.dataset d ON d.dataset_id = dc.dataset_id
LEFT JOIN public.eval_case ec ON ec.dataset_case_pk = dc.case_pk
LEFT JOIN public.case_expectation ce ON ce.eval_case_id = ec.eval_case_id
LEFT JOIN raw.case_intervention ci ON ci.case_pk = dc.case_pk
LEFT JOIN raw.model_response mresp ON mresp.case_pk = dc.case_pk
LEFT JOIN public.response resp ON resp.legacy_model_response_id = mresp.response_id
LEFT JOIN public.response_structured_decision rsd ON rsd.response_id = resp.response_id
LEFT JOIN public.run run ON run.run_id = mresp.run_id
LEFT JOIN raw.manual_score ms ON ms.response_id = mresp.response_id
LEFT JOIN public.failure_class fc ON fc.failure_class_id = ms.primary_failure_class_id
LEFT JOIN raw.deterministic_score ds ON ds.response_id = mresp.response_id
LEFT JOIN raw.source_file sf_case ON sf_case.source_file_id = dc.source_file_id
LEFT JOIN raw.source_file sf_expectation ON sf_expectation.source_file_id = ce.source_file_id
LEFT JOIN raw.source_file sf_response ON sf_response.source_file_id = mresp.source_file_id
LEFT JOIN raw.source_file sf_structured ON sf_structured.source_file_id = rsd.source_file_id
LEFT JOIN raw.source_file sf_manual ON sf_manual.source_file_id = ms.source_file_id
LEFT JOIN raw.source_file sf_deterministic ON sf_deterministic.source_file_id = ds.source_file_id;

COMMENT ON VIEW rpt.case_run_trace IS
    'Reporting trace view over public operational dataset/run/expectation/extraction metadata plus raw response, score, and source provenance rows.';

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
LEFT JOIN public.run run
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

CREATE VIEW public.run_summary AS
SELECT * FROM rpt.run_summary;

COMMENT ON VIEW public.run_summary IS
    'Compatibility view. Prefer rpt.run_summary for new run-list queries.';

CREATE VIEW public.run_detail AS
SELECT * FROM rpt.run_detail;

COMMENT ON VIEW public.run_detail IS
    'Compatibility view. Prefer rpt.run_detail for new run-detail queries.';

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
