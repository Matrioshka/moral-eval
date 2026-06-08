-- Backfill operational responses that do not have reconstructable case turns.
--
-- Prior migrations preserve a clean response -> case_turn -> eval_case chain. Some
-- imported legacy model_response rows reference dataset_case rows for which no exact
-- scenario/follow-up turn text was reconstructed into case_turn. Rather than omit
-- those valid responses, this migration creates an explicit placeholder turn type:
--
--   unresolved_legacy_prompt
--
-- This is deliberately not reported as exact prompt text. It is an operational anchor
-- for imported responses whose precise prompt/turn should be recovered from legacy
-- raw rows/source files if needed.

BEGIN;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- Ensure the placeholder turn type exists.
INSERT INTO turn_type (slug, name, description)
VALUES (
    'unresolved_legacy_prompt',
    'Unresolved legacy prompt',
    'Operational placeholder turn for imported legacy responses whose exact source prompt/turn text was not reconstructed into scenario, baseline_question, or followup. Not exact source prompt text.'
)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = now();

-- Ensure no-pressure exists.
INSERT INTO pressure_type (slug, name, description, pressure_family)
VALUES (
    'none',
    'No pressure',
    'No explicit operator, user, institutional, urgency, reputational, or reassurance pressure is present.',
    'none'
)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    pressure_family = EXCLUDED.pressure_family,
    updated_at = now();

-- Ensure eval_case exists for every valid legacy response.
WITH valid_response_cases AS (
    SELECT dc.*
    FROM dataset_case dc
    WHERE EXISTS (
        SELECT 1
        FROM model_response mr
        WHERE mr.case_pk = dc.case_pk
          AND mr.raw_response IS NOT NULL
          AND length(trim(mr.raw_response)) > 0
    )
), eval_case_source AS (
    SELECT
        dc.case_pk,
        dc.dataset_id,
        dc.sample_id,
        dc.case_id,
        dc.source_item_id,
        dc.scenario_id,
        md.moral_domain_id,
        dc.initial_judgement,
        dc.source_file_id,
        jsonb_build_object(
            'legacy_table', 'dataset_case',
            'case_origin', dc.case_origin,
            'is_canonical_dataset_item', dc.is_canonical_dataset_item,
            'raw_record', dc.raw_record,
            'backfill_migration', '013'
        ) AS raw_metadata
    FROM valid_response_cases dc
    LEFT JOIN moral_domain md
        ON md.slug = regexp_replace(lower(trim(dc.moral_domain::text)), '[^a-z0-9]+', '_', 'g')
)
INSERT INTO eval_case (
    dataset_case_pk,
    dataset_id,
    sample_id,
    case_id,
    source_item_id,
    scenario_id,
    moral_domain_id,
    initial_judgement,
    source_file_id,
    raw_metadata
)
SELECT
    case_pk,
    dataset_id,
    sample_id,
    case_id,
    source_item_id,
    scenario_id,
    moral_domain_id,
    initial_judgement,
    source_file_id,
    raw_metadata
FROM eval_case_source
ON CONFLICT (dataset_case_pk) DO UPDATE SET
    dataset_id = EXCLUDED.dataset_id,
    sample_id = EXCLUDED.sample_id,
    case_id = EXCLUDED.case_id,
    source_item_id = EXCLUDED.source_item_id,
    scenario_id = EXCLUDED.scenario_id,
    moral_domain_id = EXCLUDED.moral_domain_id,
    initial_judgement = EXCLUDED.initial_judgement,
    source_file_id = EXCLUDED.source_file_id,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

-- Create one unresolved prompt placeholder turn per eval_case that has valid legacy
-- model responses but no existing response-linked case_turn available for those rows.
INSERT INTO case_turn (
    eval_case_id,
    parent_turn_id,
    turn_index,
    turn_type,
    turn_type_id,
    turn_text,
    intervention_role,
    pressure_type,
    pressure_type_id,
    source_case_pk,
    source_column,
    raw_metadata
)
SELECT
    ec.eval_case_id,
    NULL::bigint,
    0,
    'unresolved_legacy_prompt',
    tt.turn_type_id,
    '[Unresolved legacy prompt: exact prompt text was not reconstructed from the imported artefact. See linked model_response.raw_row, source_file, and provenance lineage.]'::text,
    'user',
    'none',
    pt.pressure_type_id,
    dc.case_pk,
    'model_response.unresolved_legacy_prompt',
    jsonb_build_object(
        'backfill_migration', '013',
        'meaning', 'placeholder turn for valid imported model_response rows lacking reconstructed case_turn text',
        'exact_source_text', false
    )
FROM eval_case ec
JOIN dataset_case dc ON dc.case_pk = ec.dataset_case_pk
JOIN turn_type tt ON tt.slug = 'unresolved_legacy_prompt'
JOIN pressure_type pt ON pt.slug = 'none'
WHERE EXISTS (
    SELECT 1
    FROM model_response mr
    WHERE mr.case_pk = dc.case_pk
      AND mr.raw_response IS NOT NULL
      AND length(trim(mr.raw_response)) > 0
)
AND NOT EXISTS (
    SELECT 1
    FROM case_turn ct
    WHERE ct.source_case_pk = dc.case_pk
      AND ct.source_column = 'model_response.unresolved_legacy_prompt'
)
AND NOT EXISTS (
    SELECT 1
    FROM case_turn ct
    WHERE ct.eval_case_id = ec.eval_case_id
      AND ct.source_column IN ('dataset_case.scenario', 'case_intervention.user_followup')
);

-- Backfill all remaining valid legacy model_response rows. Prefer real follow-up,
-- then baseline/scenario, then unresolved placeholder.
WITH response_source AS (
    SELECT DISTINCT ON (mr.response_id)
        selected_turn.case_turn_id,
        mr.run_id,
        mr.response_id AS legacy_model_response_id,
        'assistant' AS response_role,
        mr.raw_response AS response_text,
        mr.source_file_id,
        mr.source_row,
        jsonb_build_object(
            'legacy_table', 'model_response',
            'backfill_migration', '013',
            'sample_id', mr.sample_id,
            'case_id', mr.case_id,
            'source_item_id', mr.source_item_id,
            'dataset_version', mr.dataset_version,
            'prompt_style', mr.prompt_style,
            'raw_row', mr.raw_row
        ) AS raw_metadata
    FROM model_response mr
    JOIN eval_case ec ON ec.dataset_case_pk = mr.case_pk
    JOIN LATERAL (
        SELECT ct.case_turn_id
        FROM case_turn ct
        LEFT JOIN turn_type tt ON tt.turn_type_id = ct.turn_type_id
        WHERE ct.eval_case_id = ec.eval_case_id
        ORDER BY
            CASE
                WHEN tt.slug = 'followup' OR ct.turn_type IN ('followup', 'intervention') THEN 0
                WHEN tt.slug = 'baseline_question' OR ct.turn_type = 'baseline_question' THEN 1
                WHEN tt.slug = 'scenario' OR ct.turn_type = 'scenario' THEN 2
                WHEN tt.slug = 'unresolved_legacy_prompt' OR ct.turn_type = 'unresolved_legacy_prompt' THEN 3
                ELSE 4
            END,
            ct.turn_index DESC,
            ct.case_turn_id DESC
        LIMIT 1
    ) selected_turn ON true
    WHERE mr.raw_response IS NOT NULL
      AND length(trim(mr.raw_response)) > 0
    ORDER BY mr.response_id, mr.source_file_id NULLS LAST, mr.source_row NULLS LAST
)
INSERT INTO response (
    case_turn_id,
    run_id,
    legacy_model_response_id,
    response_role,
    response_text,
    source_file_id,
    source_row,
    raw_metadata
)
SELECT
    case_turn_id,
    run_id,
    legacy_model_response_id,
    response_role,
    response_text,
    source_file_id,
    source_row,
    raw_metadata
FROM response_source
ON CONFLICT (legacy_model_response_id) DO UPDATE SET
    case_turn_id = EXCLUDED.case_turn_id,
    run_id = EXCLUDED.run_id,
    response_role = EXCLUDED.response_role,
    response_text = EXCLUDED.response_text,
    source_file_id = EXCLUDED.source_file_id,
    source_row = EXCLUDED.source_row,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

-- Backfill manual score events now response coverage is broader.
CREATE UNIQUE INDEX IF NOT EXISTS ux_score_event_legacy_manual
    ON score_event(legacy_manual_score_id)
    WHERE legacy_manual_score_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_score_event_legacy_deterministic
    ON score_event(legacy_deterministic_score_id)
    WHERE legacy_deterministic_score_id IS NOT NULL;

WITH manual_score_source AS (
    SELECT DISTINCT ON (ms.manual_score_id)
        r.response_id,
        sc.scorer_id,
        ms.rubric_id,
        ms.primary_failure_class_id AS failure_class_id,
        ms.score_0_to_3 AS score,
        NULLIF(trim(ms.action), '') AS label,
        NULLIF(trim(ms.notes), '') AS rationale,
        'Imported from manual_score after operational response backfill; original row retained in raw_metadata and legacy_manual_score_id.' AS notes,
        CASE
            WHEN ms.confidence ~ '^[0-9]+(\.[0-9]+)?$' THEN ms.confidence::numeric
            ELSE NULL::numeric
        END AS confidence,
        CASE
            WHEN ms.confidence IS NOT NULL AND ms.confidence !~ '^[0-9]+(\.[0-9]+)?$' THEN ms.confidence
            ELSE NULL::text
        END AS confidence_label,
        ms.source_file_id,
        ms.source_row,
        ms.manual_score_id AS legacy_manual_score_id,
        jsonb_build_object(
            'legacy_table', 'manual_score',
            'backfill_migration', '013',
            'action', ms.action,
            'confidence', ms.confidence,
            'raw_row', ms.raw_row
        ) AS raw_metadata
    FROM manual_score ms
    JOIN response r ON r.legacy_model_response_id = ms.response_id
    JOIN scorer sc
      ON sc.scorer_type = 'imported_manual_audit'
     AND sc.name = 'Imported manual audit'
     AND sc.model_name IS NULL
    ORDER BY ms.manual_score_id, ms.source_file_id NULLS LAST, ms.source_row NULLS LAST
)
INSERT INTO score_event (
    response_id,
    scorer_id,
    rubric_id,
    failure_class_id,
    score,
    label,
    rationale,
    notes,
    confidence,
    confidence_label,
    source_file_id,
    source_row,
    legacy_manual_score_id,
    raw_metadata
)
SELECT
    response_id,
    scorer_id,
    rubric_id,
    failure_class_id,
    score,
    label,
    rationale,
    notes,
    confidence,
    confidence_label,
    source_file_id,
    source_row,
    legacy_manual_score_id,
    raw_metadata
FROM manual_score_source
ON CONFLICT (legacy_manual_score_id) WHERE legacy_manual_score_id IS NOT NULL DO UPDATE SET
    response_id = EXCLUDED.response_id,
    scorer_id = EXCLUDED.scorer_id,
    rubric_id = EXCLUDED.rubric_id,
    failure_class_id = EXCLUDED.failure_class_id,
    score = EXCLUDED.score,
    label = EXCLUDED.label,
    rationale = EXCLUDED.rationale,
    notes = EXCLUDED.notes,
    confidence = EXCLUDED.confidence,
    confidence_label = EXCLUDED.confidence_label,
    source_file_id = EXCLUDED.source_file_id,
    source_row = EXCLUDED.source_row,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

-- Backfill deterministic scores where they can now link to responses.
WITH deterministic_score_source AS (
    SELECT DISTINCT ON (ds.deterministic_score_id)
        r.response_id,
        sc.scorer_id,
        rb.rubric_id,
        NULL::bigint AS failure_class_id,
        CASE
            WHEN ds.score_value ~ '^[+-]?[0-9]+(\.[0-9]+)?$' THEN ds.score_value::numeric
            ELSE NULL::numeric
        END AS score,
        COALESCE(NULLIF(trim(ds.answer), ''), NULLIF(trim(ds.score_value), '')) AS label,
        NULLIF(trim(ds.explanation), '') AS rationale,
        'Imported from deterministic_score after operational response backfill. Treat as a programmatic diagnostic/check rather than a human moral audit.' AS notes,
        NULL::numeric AS confidence,
        NULL::text AS confidence_label,
        ds.source_file_id,
        ds.source_row,
        ds.deterministic_score_id AS legacy_deterministic_score_id,
        jsonb_build_object(
            'legacy_table', 'deterministic_score',
            'backfill_migration', '013',
            'scorer_name', ds.scorer_name,
            'score_value', ds.score_value,
            'answer', ds.answer,
            'metadata', ds.metadata,
            'raw_row', ds.raw_row
        ) AS raw_metadata
    FROM deterministic_score ds
    JOIN response r ON r.legacy_model_response_id = ds.response_id
    JOIN scorer sc
      ON sc.scorer_type = 'deterministic'
     AND sc.name = COALESCE(NULLIF(trim(ds.scorer_name), ''), 'deterministic_scorer')
     AND sc.model_name IS NULL
    JOIN rubric rb ON rb.rubric_name = 'deterministic_schema_check'
    ORDER BY ds.deterministic_score_id, ds.source_file_id NULLS LAST, ds.source_row NULLS LAST
)
INSERT INTO score_event (
    response_id,
    scorer_id,
    rubric_id,
    failure_class_id,
    score,
    label,
    rationale,
    notes,
    confidence,
    confidence_label,
    source_file_id,
    source_row,
    legacy_deterministic_score_id,
    raw_metadata
)
SELECT
    response_id,
    scorer_id,
    rubric_id,
    failure_class_id,
    score,
    label,
    rationale,
    notes,
    confidence,
    confidence_label,
    source_file_id,
    source_row,
    legacy_deterministic_score_id,
    raw_metadata
FROM deterministic_score_source
ON CONFLICT (legacy_deterministic_score_id) WHERE legacy_deterministic_score_id IS NOT NULL DO UPDATE SET
    response_id = EXCLUDED.response_id,
    scorer_id = EXCLUDED.scorer_id,
    rubric_id = EXCLUDED.rubric_id,
    failure_class_id = EXCLUDED.failure_class_id,
    score = EXCLUDED.score,
    label = EXCLUDED.label,
    rationale = EXCLUDED.rationale,
    notes = EXCLUDED.notes,
    source_file_id = EXCLUDED.source_file_id,
    source_row = EXCLUDED.source_row,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

SELECT 'legacy_model_response_rows' AS metric, count(*) AS value
FROM model_response
UNION ALL
SELECT 'valid_legacy_model_responses_without_operational_response', count(*)
FROM model_response mr
WHERE mr.raw_response IS NOT NULL
  AND length(trim(mr.raw_response)) > 0
  AND NOT EXISTS (
      SELECT 1 FROM response r WHERE r.legacy_model_response_id = mr.response_id
  )
UNION ALL
SELECT 'operational_response_rows', count(*)
FROM response
UNION ALL
SELECT 'unresolved_legacy_prompt_turns', count(*)
FROM case_turn ct
LEFT JOIN turn_type tt ON tt.turn_type_id = ct.turn_type_id
WHERE tt.slug = 'unresolved_legacy_prompt'
   OR ct.turn_type = 'unresolved_legacy_prompt'
UNION ALL
SELECT 'manual_score_rows', count(*)
FROM manual_score
UNION ALL
SELECT 'manual_scores_without_score_event', count(*)
FROM manual_score ms
WHERE NOT EXISTS (
    SELECT 1 FROM score_event se WHERE se.legacy_manual_score_id = ms.manual_score_id
)
UNION ALL
SELECT 'deterministic_score_rows', count(*)
FROM deterministic_score
UNION ALL
SELECT 'deterministic_scores_without_score_event', count(*)
FROM deterministic_score ds
WHERE NOT EXISTS (
    SELECT 1 FROM score_event se WHERE se.legacy_deterministic_score_id = ds.deterministic_score_id
)
UNION ALL
SELECT 'score_event_rows', count(*)
FROM score_event
ORDER BY metric;

COMMIT;
