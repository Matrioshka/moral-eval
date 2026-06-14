-- Backfill public operational tables from raw/import provenance tables.
--
-- This is the split-schema successor to the data-population parts of the current
-- public-layout rebuild path. It intentionally does not create a full bootstrap
-- schema. Expected preconditions:
--
-- - raw import/source tables already exist and are populated.
-- - public operational tables and lookup tables already exist.
-- - FKs from public operational tables to imported provenance objects are either
--   already raw-aware or were preserved by ALTER TABLE ... SET SCHEMA moves.
-- - sql/015_create_rpt_reporting_views.sql should be applied after this file if
--   reporting views need to be refreshed.
--
-- It reads imported/source-shaped rows from raw.*, writes operational rows to
-- public.*, and does not move tables or mutate source artefacts.

BEGIN;

-- -----------------------------------------------------------------------------
-- Scenario normalisation from raw.dataset_case into public.scenario.
-- -----------------------------------------------------------------------------
WITH distinct_scenarios AS (
    SELECT
        trim(dc.scenario::text) AS scenario_text,
        min(dc.source_file_id) AS source_file_id,
        count(*) AS dataset_case_count
    FROM raw.dataset_case dc
    WHERE dc.scenario IS NOT NULL
      AND length(trim(dc.scenario::text)) > 0
    GROUP BY trim(dc.scenario::text)
), prepared AS (
    SELECT
        md5(scenario_text) AS scenario_hash,
        left(regexp_replace(scenario_text, '\s+', ' ', 'g'), 120) AS name,
        scenario_text,
        NULL::text AS description,
        'dataset_case'::text AS source_name,
        'raw.dataset_case.scenario'::text AS source_reference,
        NULL::text AS source_uri,
        source_file_id
    FROM distinct_scenarios
)
INSERT INTO public.scenario AS scenario (
    scenario_hash,
    name,
    scenario_text,
    description,
    source_name,
    source_reference,
    source_uri,
    source_file_id
)
SELECT
    scenario_hash,
    name,
    scenario_text,
    description,
    source_name,
    source_reference,
    source_uri,
    source_file_id
FROM prepared
ON CONFLICT (scenario_hash) DO UPDATE SET
    scenario_text = EXCLUDED.scenario_text,
    source_file_id = COALESCE(scenario.source_file_id, EXCLUDED.source_file_id),
    source_name = COALESCE(scenario.source_name, EXCLUDED.source_name),
    source_reference = COALESCE(scenario.source_reference, EXCLUDED.source_reference),
    source_uri = COALESCE(scenario.source_uri, EXCLUDED.source_uri),
    updated_at = now();

-- Update scenario names from case metadata, preserving exact scenario_text.
WITH scenario_context AS (
    SELECT
        s.scenario_id,
        s.scenario_text,
        min(dc.moral_domain) FILTER (
            WHERE dc.moral_domain IS NOT NULL
              AND length(trim(dc.moral_domain::text)) > 0
        ) AS moral_domain,
        min(dc.case_id) FILTER (
            WHERE dc.case_id IS NOT NULL
              AND length(trim(dc.case_id::text)) > 0
              AND COALESCE(dc.is_canonical_dataset_item, false) = true
        ) AS canonical_case_id,
        min(dc.case_id) FILTER (
            WHERE dc.case_id IS NOT NULL
              AND length(trim(dc.case_id::text)) > 0
        ) AS any_case_id,
        min(dc.source_item_id) FILTER (
            WHERE dc.source_item_id IS NOT NULL
              AND length(trim(dc.source_item_id::text)) > 0
        ) AS source_item_id
    FROM public.scenario s
    JOIN raw.dataset_case dc
      ON md5(trim(dc.scenario::text)) = s.scenario_hash
    WHERE dc.scenario IS NOT NULL
      AND length(trim(dc.scenario::text)) > 0
    GROUP BY s.scenario_id, s.scenario_text
), chosen_identifier AS (
    SELECT
        scenario_id,
        scenario_text,
        COALESCE(canonical_case_id, any_case_id, source_item_id, moral_domain, 'scenario') AS identifier
    FROM scenario_context
), cleaned AS (
    SELECT
        scenario_id,
        scenario_text,
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(lower(identifier), '^v[0-9]+_', ''),
                        '_manual_scores$', ''
                    ),
                    '_outputs$', ''
                ),
                '_structured_access_decision(_v[0-9_]+)?$', ''
            ),
            '_+', ' ', 'g'
        ) AS cleaned_identifier
    FROM chosen_identifier
), titled AS (
    SELECT
        scenario_id,
        scenario_text,
        regexp_replace(
            replace(
                replace(
                    replace(
                        replace(
                            replace(
                                replace(
                                    replace(initcap(cleaned_identifier), ' Ai ', ' AI '),
                                    'Ai ', 'AI '
                                ),
                                ' Api ', ' API '
                            ),
                            'Api ', 'API '
                        ),
                        ' Pii ', ' PII '
                    ),
                    'Pii ', 'PII '
                ),
                ' Gpt ', ' GPT '
            ),
            '\s+', ' ', 'g'
        ) AS generated_name
    FROM cleaned
), final_names AS (
    SELECT
        scenario_id,
        CASE
            WHEN length(trim(generated_name)) BETWEEN 8 AND 80 THEN trim(generated_name)
            ELSE left(regexp_replace(scenario_text, '\s+', ' ', 'g'), 80)
        END AS scenario_name
    FROM titled
)
UPDATE public.scenario s
SET
    name = final_names.scenario_name,
    updated_at = now()
FROM final_names
WHERE s.scenario_id = final_names.scenario_id
  AND s.name IS DISTINCT FROM final_names.scenario_name;

-- -----------------------------------------------------------------------------
-- Evidence quality lookup enrichment from raw.case_intervention.
-- -----------------------------------------------------------------------------
INSERT INTO public.evidence_quality (slug, name, description, strength_rank)
VALUES
    ('irrelevant_reassurance', 'Irrelevant reassurance', 'Reassurance, authority, preference, or contextual pressure that does not materially reduce the morally relevant risk or answer the original concern.', 0),
    ('weak_safeguard', 'Weak safeguard', 'A limited or superficial safeguard that may slightly reduce risk but is insufficient to justify a major moral, safety, or governance update.', 1),
    ('weak_or_cosmetic_safeguard', 'Weak or cosmetic safeguard', 'A weak, cosmetic, or process-only safeguard that should not be treated as resolving the substantive concern.', 1),
    ('strong_evidence_update', 'Strong evidence update', 'Strong and relevant evidence that justifies a meaningful update, while still requiring calibration to residual uncertainty.', 3),
    ('strong_but_incomplete_safeguard', 'Strong but incomplete safeguard', 'A substantively relevant safeguard that reduces some important risks but leaves enough residual uncertainty that full approval would be overconfident.', 3),
    ('near_sufficient_safeguard', 'Near-sufficient safeguard', 'Evidence or safeguards that are close to sufficient for the target judgement, though still requiring acknowledgement of remaining limits.', 4)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    strength_rank = EXCLUDED.strength_rank,
    updated_at = now();

INSERT INTO public.evidence_quality (slug, name)
SELECT DISTINCT
    regexp_replace(lower(trim(ci.evidence_quality::text)), '[^a-z0-9]+', '_', 'g') AS slug,
    initcap(replace(regexp_replace(lower(trim(ci.evidence_quality::text)), '[^a-z0-9]+', '_', 'g'), '_', ' ')) AS name
FROM raw.case_intervention ci
WHERE ci.evidence_quality IS NOT NULL
  AND length(trim(ci.evidence_quality::text)) > 0
ON CONFLICT (slug) DO NOTHING;

-- -----------------------------------------------------------------------------
-- eval_case backfill.
-- -----------------------------------------------------------------------------
INSERT INTO public.eval_case (
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
    dc.case_pk,
    dc.dataset_id,
    dc.sample_id,
    dc.case_id,
    dc.source_item_id,
    s.scenario_id,
    md.moral_domain_id,
    dc.initial_judgement,
    dc.source_file_id,
    jsonb_build_object(
        'legacy_table', 'raw.dataset_case',
        'case_origin', dc.case_origin,
        'is_canonical_dataset_item', dc.is_canonical_dataset_item,
        'raw_record', dc.raw_record,
        'backfill_migration', '016'
    )
FROM raw.dataset_case dc
LEFT JOIN public.scenario s
    ON dc.scenario IS NOT NULL
   AND length(trim(dc.scenario::text)) > 0
   AND s.scenario_hash = md5(trim(dc.scenario::text))
LEFT JOIN public.moral_domain md
    ON md.slug = regexp_replace(lower(trim(dc.moral_domain::text)), '[^a-z0-9]+', '_', 'g')
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
-- case_turn backfill for scenario and follow-up/intervention turns.
-- -----------------------------------------------------------------------------
INSERT INTO public.case_turn (
    eval_case_id,
    parent_turn_id,
    turn_index,
    turn_type,
    turn_text,
    intervention_role,
    source_case_pk,
    source_column,
    raw_metadata
)
SELECT
    ec.eval_case_id,
    NULL::bigint,
    0,
    'scenario',
    COALESCE(s.scenario_text, dc.scenario),
    'user',
    dc.case_pk,
    'dataset_case.scenario',
    jsonb_build_object('legacy_table', 'raw.dataset_case', 'backfill_migration', '016')
FROM public.eval_case ec
JOIN raw.dataset_case dc ON dc.case_pk = ec.dataset_case_pk
LEFT JOIN public.scenario s ON s.scenario_id = ec.scenario_id
WHERE COALESCE(s.scenario_text, dc.scenario) IS NOT NULL
  AND length(trim(COALESCE(s.scenario_text, dc.scenario))) > 0
ON CONFLICT DO NOTHING;

INSERT INTO public.case_turn (
    eval_case_id,
    parent_turn_id,
    turn_index,
    turn_type,
    turn_text,
    intervention_role,
    pressure_type,
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
    'intervention',
    ci.user_followup,
    'user',
    ci.pressure_type,
    eq.evidence_quality_id,
    dc.case_pk,
    ci.intervention_id,
    'case_intervention.user_followup',
    jsonb_build_object(
        'legacy_table', 'raw.case_intervention',
        'backfill_migration', '016',
        'followup_strength', ci.followup_strength,
        'pressure_source', ci.pressure_source,
        'pressure_mechanism', ci.pressure_mechanism,
        'pressure_legitimacy', ci.pressure_legitimacy,
        'pressure_escalation_stage', ci.pressure_escalation_stage,
        'pressure_target', ci.pressure_target,
        'conflict_type', ci.conflict_type,
        'raw_metadata', ci.raw_metadata
    )
FROM public.eval_case ec
JOIN raw.dataset_case dc ON dc.case_pk = ec.dataset_case_pk
JOIN raw.case_intervention ci ON ci.case_pk = dc.case_pk
LEFT JOIN public.evidence_quality eq
    ON ci.evidence_quality IS NOT NULL
   AND eq.slug = regexp_replace(lower(trim(ci.evidence_quality::text)), '[^a-z0-9]+', '_', 'g')
LEFT JOIN public.case_turn parent
    ON parent.eval_case_id = ec.eval_case_id
   AND parent.turn_type = 'scenario'
   AND parent.source_column = 'dataset_case.scenario'
WHERE ci.user_followup IS NOT NULL
  AND length(trim(ci.user_followup)) > 0
ON CONFLICT DO NOTHING;

-- Link case_turn rows to turn/pressure lookups where those lookup tables exist.
UPDATE public.case_turn ct
SET turn_type_id = tt.turn_type_id
FROM public.turn_type tt
WHERE tt.slug = CASE
        WHEN regexp_replace(lower(trim(ct.turn_type::text)), '[^a-z0-9]+', '_', 'g') IN ('intervention', 'pressure', 'evidence_update') THEN 'followup'
        ELSE regexp_replace(lower(trim(ct.turn_type::text)), '[^a-z0-9]+', '_', 'g')
    END
  AND ct.turn_type_id IS DISTINCT FROM tt.turn_type_id;

UPDATE public.case_turn ct
SET pressure_type_id = pt.pressure_type_id
FROM public.pressure_type pt
WHERE pt.slug = CASE
        WHEN ct.pressure_type IS NULL OR length(trim(ct.pressure_type::text)) = 0 THEN 'none'
        WHEN regexp_replace(lower(trim(ct.pressure_type::text)), '[^a-z0-9]+', '_', 'g') IN ('none', 'no_pressure', 'no') THEN 'none'
        WHEN regexp_replace(lower(trim(ct.pressure_type::text)), '[^a-z0-9]+', '_', 'g') IN ('authority', 'authority_pressure', 'seniority_pressure', 'authority_seniority_pressure') THEN 'authority_seniority'
        WHEN regexp_replace(lower(trim(ct.pressure_type::text)), '[^a-z0-9]+', '_', 'g') IN ('urgency', 'deployment', 'urgency_pressure', 'deployment_pressure', 'deployment_urgency') THEN 'urgency_deployment'
        WHEN regexp_replace(lower(trim(ct.pressure_type::text)), '[^a-z0-9]+', '_', 'g') IN ('emotional', 'reputational', 'emotional_pressure', 'reputational_pressure', 'emotional_reputational_pressure') THEN 'emotional_reputational'
        WHEN regexp_replace(lower(trim(ct.pressure_type::text)), '[^a-z0-9]+', '_', 'g') IN ('consensus', 'institutional', 'institutional_pressure', 'consensus_pressure') THEN 'institutional_consensus'
        ELSE regexp_replace(lower(trim(ct.pressure_type::text)), '[^a-z0-9]+', '_', 'g')
    END
  AND ct.pressure_type_id IS DISTINCT FROM pt.pressure_type_id;

-- -----------------------------------------------------------------------------
-- Operational response backfill.
-- -----------------------------------------------------------------------------
INSERT INTO public.turn_type (slug, name, description)
VALUES (
    'unresolved_legacy_prompt',
    'Unresolved legacy prompt',
    'Operational placeholder turn for imported legacy responses whose exact source prompt/turn text was not reconstructed into scenario, baseline_question, or followup. Not exact source prompt text.'
)
ON CONFLICT (slug) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    updated_at = now();

INSERT INTO public.pressure_type (slug, name, description, pressure_family)
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

INSERT INTO public.case_turn (
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
        'backfill_migration', '016',
        'meaning', 'placeholder turn for valid imported model_response rows lacking reconstructed case_turn text',
        'exact_source_text', false
    )
FROM public.eval_case ec
JOIN raw.dataset_case dc ON dc.case_pk = ec.dataset_case_pk
JOIN public.turn_type tt ON tt.slug = 'unresolved_legacy_prompt'
JOIN public.pressure_type pt ON pt.slug = 'none'
WHERE EXISTS (
    SELECT 1
    FROM raw.model_response mr
    WHERE mr.case_pk = dc.case_pk
      AND mr.raw_response IS NOT NULL
      AND length(trim(mr.raw_response)) > 0
)
AND NOT EXISTS (
    SELECT 1
    FROM public.case_turn ct
    WHERE ct.source_case_pk = dc.case_pk
      AND ct.source_column = 'model_response.unresolved_legacy_prompt'
)
AND NOT EXISTS (
    SELECT 1
    FROM public.case_turn ct
    WHERE ct.eval_case_id = ec.eval_case_id
      AND ct.source_column IN ('dataset_case.scenario', 'case_intervention.user_followup')
);

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
            'legacy_table', 'raw.model_response',
            'backfill_migration', '016',
            'sample_id', mr.sample_id,
            'case_id', mr.case_id,
            'source_item_id', mr.source_item_id,
            'dataset_version', mr.dataset_version,
            'prompt_style', mr.prompt_style,
            'raw_row', mr.raw_row
        ) AS raw_metadata
    FROM raw.model_response mr
    JOIN public.eval_case ec ON ec.dataset_case_pk = mr.case_pk
    JOIN LATERAL (
        SELECT ct.case_turn_id
        FROM public.case_turn ct
        LEFT JOIN public.turn_type tt ON tt.turn_type_id = ct.turn_type_id
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
INSERT INTO public.response (
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
-- Scorer/rubric/score_event backfill.
-- -----------------------------------------------------------------------------
INSERT INTO public.scorer (scorer_type, name, model_name, description)
SELECT 'imported_manual_audit', 'Imported manual audit', NULL::text,
       'Scores imported from project manual-audit CSV artefacts. The original audit file remains the source-of-truth artefact.'
WHERE NOT EXISTS (
    SELECT 1 FROM public.scorer
    WHERE scorer_type = 'imported_manual_audit'
      AND name = 'Imported manual audit'
      AND model_name IS NULL
);

INSERT INTO public.scorer (scorer_type, name, model_name, description)
SELECT 'deterministic', 'schema_v2_1_scorer', NULL::text,
       'Deterministic schema-v2.1 scorer/checker for structured tuple extraction, field validity, and bound/consistency checks.'
WHERE NOT EXISTS (
    SELECT 1 FROM public.scorer
    WHERE scorer_type = 'deterministic'
      AND name = 'schema_v2_1_scorer'
      AND model_name IS NULL
);

INSERT INTO public.scorer (scorer_type, name, model_name, description)
SELECT DISTINCT
    'deterministic' AS scorer_type,
    COALESCE(NULLIF(trim(ds.scorer_name), ''), 'deterministic_scorer') AS name,
    NULL::text AS model_name,
    'Deterministic scorer/checker imported from raw.deterministic_score.scorer_name.' AS description
FROM raw.deterministic_score ds
WHERE NOT EXISTS (
    SELECT 1 FROM public.scorer sc
    WHERE sc.scorer_type = 'deterministic'
      AND sc.name = COALESCE(NULLIF(trim(ds.scorer_name), ''), 'deterministic_scorer')
      AND sc.model_name IS NULL
);

INSERT INTO public.rubric (name, score_scale, description)
VALUES (
    'deterministic_schema_check',
    'mixed numeric/text diagnostic',
    'Programmatic checks over structured model outputs, including tuple extraction, schema validity, bounds checking, and related diagnostics. These are not independent human moral judgements.'
)
ON CONFLICT (name) DO NOTHING;

CREATE UNIQUE INDEX IF NOT EXISTS ux_score_event_legacy_manual
    ON public.score_event(legacy_manual_score_id)
    WHERE legacy_manual_score_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_score_event_legacy_deterministic
    ON public.score_event(legacy_deterministic_score_id)
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
        'Imported from raw.manual_score after operational response backfill; original row retained in raw_metadata and legacy_manual_score_id.' AS notes,
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
            'legacy_table', 'raw.manual_score',
            'backfill_migration', '016',
            'action', ms.action,
            'confidence', ms.confidence,
            'raw_row', ms.raw_row
        ) AS raw_metadata
    FROM raw.manual_score ms
    JOIN public.response r ON r.legacy_model_response_id = ms.response_id
    JOIN public.scorer sc
      ON sc.scorer_type = 'imported_manual_audit'
     AND sc.name = 'Imported manual audit'
     AND sc.model_name IS NULL
    ORDER BY ms.manual_score_id, ms.source_file_id NULLS LAST, ms.source_row NULLS LAST
)
INSERT INTO public.score_event (
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
        'Imported from raw.deterministic_score after operational response backfill. Treat as a programmatic diagnostic/check rather than a human moral audit.' AS notes,
        NULL::numeric AS confidence,
        NULL::text AS confidence_label,
        ds.source_file_id,
        ds.source_row,
        ds.deterministic_score_id AS legacy_deterministic_score_id,
        jsonb_build_object(
            'legacy_table', 'raw.deterministic_score',
            'backfill_migration', '016',
            'scorer_name', ds.scorer_name,
            'score_value', ds.score_value,
            'answer', ds.answer,
            'metadata', ds.metadata,
            'raw_row', ds.raw_row
        ) AS raw_metadata
    FROM raw.deterministic_score ds
    JOIN public.response r ON r.legacy_model_response_id = ds.response_id
    JOIN public.scorer sc
      ON sc.scorer_type = 'deterministic'
     AND sc.name = COALESCE(NULLIF(trim(ds.scorer_name), ''), 'deterministic_scorer')
     AND sc.model_name IS NULL
    JOIN public.rubric rb ON rb.name = 'deterministic_schema_check'
    ORDER BY ds.deterministic_score_id, ds.source_file_id NULLS LAST, ds.source_row NULLS LAST
)
INSERT INTO public.score_event (
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

SELECT 'raw_model_response_rows' AS metric, count(*) AS value
FROM raw.model_response
UNION ALL
SELECT 'valid_raw_model_responses_without_operational_response', count(*)
FROM raw.model_response mr
WHERE mr.raw_response IS NOT NULL
  AND length(trim(mr.raw_response)) > 0
  AND NOT EXISTS (
      SELECT 1 FROM public.response r WHERE r.legacy_model_response_id = mr.response_id
  )
UNION ALL
SELECT 'operational_response_rows', count(*)
FROM public.response
UNION ALL
SELECT 'unresolved_legacy_prompt_turns', count(*)
FROM public.case_turn ct
LEFT JOIN public.turn_type tt ON tt.turn_type_id = ct.turn_type_id
WHERE tt.slug = 'unresolved_legacy_prompt'
   OR ct.turn_type = 'unresolved_legacy_prompt'
UNION ALL
SELECT 'raw_manual_score_rows', count(*)
FROM raw.manual_score
UNION ALL
SELECT 'manual_scores_without_score_event', count(*)
FROM raw.manual_score ms
WHERE NOT EXISTS (
    SELECT 1 FROM public.score_event se WHERE se.legacy_manual_score_id = ms.manual_score_id
)
UNION ALL
SELECT 'raw_deterministic_score_rows', count(*)
FROM raw.deterministic_score
UNION ALL
SELECT 'deterministic_scores_without_score_event', count(*)
FROM raw.deterministic_score ds
WHERE NOT EXISTS (
    SELECT 1 FROM public.score_event se WHERE se.legacy_deterministic_score_id = ds.deterministic_score_id
)
UNION ALL
SELECT 'score_event_rows', count(*)
FROM public.score_event
ORDER BY metric;

COMMIT;
