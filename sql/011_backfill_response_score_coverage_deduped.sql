-- Backfill operational response and score_event coverage from legacy provenance rows.
--
-- Supersedes 010_backfill_all_operational_responses_and_scores.sql, which could
-- fail when multiple legacy rows collapsed onto the same operational key during an
-- INSERT ... ON CONFLICT DO UPDATE statement.
--
-- This migration deduplicates operational case sources before upsert, preserves
-- original provenance tables, and keeps score_event response-linked.

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

-- -----------------------------------------------------------------------------
-- Ensure required operational columns and lookup links exist.
-- -----------------------------------------------------------------------------
ALTER TABLE case_turn
    ADD COLUMN IF NOT EXISTS turn_type_id INTEGER;

ALTER TABLE case_turn
    ADD COLUMN IF NOT EXISTS pressure_type_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_case_turn_turn_type_id
    ON case_turn(turn_type_id);

CREATE INDEX IF NOT EXISTS idx_case_turn_pressure_type_id
    ON case_turn(pressure_type_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_case_turn_turn_type'
    ) THEN
        ALTER TABLE case_turn
            ADD CONSTRAINT fk_case_turn_turn_type
            FOREIGN KEY (turn_type_id)
            REFERENCES turn_type(turn_type_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_case_turn_pressure_type'
    ) THEN
        ALTER TABLE case_turn
            ADD CONSTRAINT fk_case_turn_pressure_type
            FOREIGN KEY (pressure_type_id)
            REFERENCES pressure_type(pressure_type_id);
    END IF;
END $$;

-- Ensure the deterministic scorer and rubric rows required below exist.
INSERT INTO scorer (scorer_type, name, model_name, description)
VALUES
    ('imported_manual_audit', 'Imported manual audit', NULL, 'Scores imported from project manual-audit CSV artefacts. The original audit file remains the source-of-truth artefact.'),
    ('deterministic', 'schema_v2_1_scorer', NULL, 'Deterministic schema-v2.1 scorer/checker for structured tuple extraction, field validity, and bound/consistency checks.'),
    ('deterministic', 'deterministic_scorer', NULL, 'Deterministic scorer/checker imported from deterministic_score.scorer_name.')
ON CONFLICT (scorer_type, name, COALESCE(model_name, '')) DO UPDATE SET
    description = EXCLUDED.description,
    updated_at = now();

INSERT INTO scorer (scorer_type, name, model_name, description)
SELECT DISTINCT
    'deterministic',
    COALESCE(NULLIF(trim(ds.scorer_name), ''), 'deterministic_scorer'),
    NULL::text,
    'Deterministic scorer/checker imported from deterministic_score.scorer_name.'
FROM deterministic_score ds
ON CONFLICT (scorer_type, name, COALESCE(model_name, '')) DO NOTHING;

INSERT INTO rubric (rubric_name, description)
VALUES
    ('deterministic_schema_check', 'Programmatic schema/tuple validity and consistency checks. These are diagnostics, not independent moral audits.')
ON CONFLICT (rubric_name) DO UPDATE SET
    description = EXCLUDED.description,
    updated_at = now();

-- -----------------------------------------------------------------------------
-- Ensure eval_case exists for every dataset_case referenced by a valid legacy
-- model_response. Use EXISTS rather than joining directly, so each dataset_case
-- appears at most once in the upsert source.
-- -----------------------------------------------------------------------------
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
            'backfill_migration', '011'
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

-- -----------------------------------------------------------------------------
-- Ensure scenario turns exist.
-- -----------------------------------------------------------------------------
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
    'scenario',
    tt.turn_type_id,
    COALESCE(s.scenario_text, dc.scenario),
    'user',
    'none',
    pt.pressure_type_id,
    dc.case_pk,
    'dataset_case.scenario',
    jsonb_build_object('legacy_table', 'dataset_case', 'backfill_migration', '011')
FROM eval_case ec
JOIN dataset_case dc ON dc.case_pk = ec.dataset_case_pk
LEFT JOIN scenario s ON s.scenario_id = ec.scenario_id
JOIN turn_type tt ON tt.slug = 'scenario'
JOIN pressure_type pt ON pt.slug = 'none'
WHERE COALESCE(s.scenario_text, dc.scenario) IS NOT NULL
  AND length(trim(COALESCE(s.scenario_text, dc.scenario))) > 0
ON CONFLICT DO NOTHING;

UPDATE case_turn ct
SET
    turn_type = 'scenario',
    turn_type_id = tt.turn_type_id,
    pressure_type = 'none',
    pressure_type_id = pt.pressure_type_id,
    updated_at = now()
FROM turn_type tt, pressure_type pt
WHERE tt.slug = 'scenario'
  AND pt.slug = 'none'
  AND ct.source_column = 'dataset_case.scenario'
  AND (
      ct.turn_type IS DISTINCT FROM 'scenario'
      OR ct.turn_type_id IS DISTINCT FROM tt.turn_type_id
      OR ct.pressure_type IS DISTINCT FROM 'none'
      OR ct.pressure_type_id IS DISTINCT FROM pt.pressure_type_id
  );

-- -----------------------------------------------------------------------------
-- Ensure follow-up turns exist. This preserves legacy source_intervention_id and
-- therefore supports later branching/multi-round turns without collapsing them.
-- -----------------------------------------------------------------------------
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
    evidence_quality_id,
    source_case_pk,
    source_intervention_id,
    source_column,
    raw_metadata
)
SELECT
    ec.eval_case_id,
    parent.case_turn_id,
    1,
    'followup',
    tt.turn_type_id,
    ci.user_followup,
    'user',
    COALESCE(NULLIF(trim(ci.pressure_type), ''), 'none'),
    COALESCE(pt.pressure_type_id, none_pt.pressure_type_id),
    ci.evidence_quality_id,
    dc.case_pk,
    ci.intervention_id,
    'case_intervention.user_followup',
    jsonb_build_object(
        'legacy_table', 'case_intervention',
        'backfill_migration', '011',
        'followup_strength', ci.followup_strength,
        'pressure_source', ci.pressure_source,
        'pressure_mechanism', ci.pressure_mechanism,
        'pressure_legitimacy', ci.pressure_legitimacy,
        'pressure_escalation_stage', ci.pressure_escalation_stage,
        'pressure_target', ci.pressure_target,
        'conflict_type', ci.conflict_type,
        'raw_metadata', ci.raw_metadata
    )
FROM eval_case ec
JOIN dataset_case dc ON dc.case_pk = ec.dataset_case_pk
JOIN case_intervention ci ON ci.case_pk = dc.case_pk
JOIN turn_type tt ON tt.slug = 'followup'
JOIN pressure_type none_pt ON none_pt.slug = 'none'
LEFT JOIN pressure_type pt
    ON pt.slug = CASE
        WHEN ci.pressure_type IS NULL OR length(trim(ci.pressure_type)) = 0 THEN 'none'
        WHEN regexp_replace(lower(trim(ci.pressure_type::text)), '[^a-z0-9]+', '_', 'g') = 'no_pressure' THEN 'none'
        ELSE regexp_replace(lower(trim(ci.pressure_type::text)), '[^a-z0-9]+', '_', 'g')
    END
LEFT JOIN case_turn parent
    ON parent.eval_case_id = ec.eval_case_id
   AND parent.source_column = 'dataset_case.scenario'
WHERE ci.user_followup IS NOT NULL
  AND length(trim(ci.user_followup)) > 0
ON CONFLICT DO NOTHING;

UPDATE case_turn ct
SET
    turn_type = 'followup',
    turn_type_id = tt.turn_type_id,
    pressure_type = COALESCE(NULLIF(trim(ci.pressure_type), ''), 'none'),
    pressure_type_id = COALESCE(pt.pressure_type_id, none_pt.pressure_type_id),
    evidence_quality_id = ci.evidence_quality_id,
    updated_at = now()
FROM case_intervention ci
JOIN turn_type tt ON tt.slug = 'followup'
JOIN pressure_type none_pt ON none_pt.slug = 'none'
LEFT JOIN pressure_type pt
    ON pt.slug = CASE
        WHEN ci.pressure_type IS NULL OR length(trim(ci.pressure_type)) = 0 THEN 'none'
        WHEN regexp_replace(lower(trim(ci.pressure_type::text)), '[^a-z0-9]+', '_', 'g') = 'no_pressure' THEN 'none'
        ELSE regexp_replace(lower(trim(ci.pressure_type::text)), '[^a-z0-9]+', '_', 'g')
    END
WHERE ct.source_intervention_id = ci.intervention_id
  AND ct.source_column = 'case_intervention.user_followup'
  AND (
      ct.turn_type IS DISTINCT FROM 'followup'
      OR ct.turn_type_id IS DISTINCT FROM tt.turn_type_id
      OR ct.pressure_type IS DISTINCT FROM COALESCE(NULLIF(trim(ci.pressure_type), ''), 'none')
      OR ct.pressure_type_id IS DISTINCT FROM COALESCE(pt.pressure_type_id, none_pt.pressure_type_id)
      OR ct.evidence_quality_id IS DISTINCT FROM ci.evidence_quality_id
  );

-- -----------------------------------------------------------------------------
-- Backfill operational responses. model_response.response_id is unique, so this
-- source should already be unique; the DISTINCT ON is a guard against damaged
-- imported state.
-- -----------------------------------------------------------------------------
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
            'backfill_migration', '011',
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
                ELSE 3
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

-- -----------------------------------------------------------------------------
-- Backfill manual score events. Deduplicate by legacy_manual_score_id before the
-- upsert so PostgreSQL never has to update the same score_event twice in one
-- statement.
-- -----------------------------------------------------------------------------
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
            'backfill_migration', '011',
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

-- -----------------------------------------------------------------------------
-- Backfill deterministic score events.
-- -----------------------------------------------------------------------------
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
            'backfill_migration', '011',
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

-- Coverage summary.
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
