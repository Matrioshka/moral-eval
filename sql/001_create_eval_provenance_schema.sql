-- PostgreSQL provenance layer for Justifiable Moral Corrigibility eval artefacts.
-- This schema is intentionally additive: existing JSONL/CSV/Markdown files remain
-- the source of truth; PostgreSQL provides queryable provenance and trace joins.

BEGIN;

CREATE TABLE IF NOT EXISTS source_file (
    source_file_id BIGSERIAL PRIMARY KEY,
    file_path TEXT NOT NULL UNIQUE,
    file_kind TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    file_size_bytes BIGINT,
    record_count INTEGER,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset (
    dataset_id BIGSERIAL PRIMARY KEY,
    dataset_version TEXT NOT NULL UNIQUE,
    dataset_family TEXT,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dataset_case (
    case_pk BIGSERIAL PRIMARY KEY,
    dataset_id BIGINT NOT NULL REFERENCES dataset(dataset_id) ON DELETE CASCADE,
    sample_id TEXT NOT NULL,
    case_id TEXT,
    source_item_id TEXT,
    variant TEXT,
    prompt_style TEXT,
    moral_domain TEXT,
    risk_track TEXT,
    scenario TEXT,
    initial_judgement TEXT,
    difficulty TEXT,
    difficulty_notes TEXT,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    source_line INTEGER,
    raw_record JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(dataset_id, sample_id)
);

CREATE INDEX IF NOT EXISTS idx_dataset_case_case_id ON dataset_case(case_id);
CREATE INDEX IF NOT EXISTS idx_dataset_case_source_item_id ON dataset_case(source_item_id);

CREATE TABLE IF NOT EXISTS case_intervention (
    intervention_id BIGSERIAL PRIMARY KEY,
    case_pk BIGINT NOT NULL UNIQUE REFERENCES dataset_case(case_pk) ON DELETE CASCADE,
    user_followup TEXT,
    evidence_quality TEXT,
    pressure_type TEXT,
    followup_strength TEXT,
    expected_update TEXT,
    pressure_source TEXT,
    pressure_mechanism TEXT,
    pressure_legitimacy TEXT,
    pressure_escalation_stage TEXT,
    pressure_target TEXT,
    conflict_type TEXT,
    safeguard_relevance_to_original_concern TEXT,
    safeguard_type JSONB NOT NULL DEFAULT '[]'::jsonb,
    safeguard_features JSONB NOT NULL DEFAULT '[]'::jsonb,
    missing_safeguards JSONB NOT NULL DEFAULT '[]'::jsonb,
    residual_risk_features JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_case_intervention_evidence ON case_intervention(evidence_quality);
CREATE INDEX IF NOT EXISTS idx_case_intervention_pressure ON case_intervention(pressure_type);

CREATE TABLE IF NOT EXISTS expected_behaviour (
    expected_behaviour_id BIGSERIAL PRIMARY KEY,
    case_pk BIGINT NOT NULL UNIQUE REFERENCES dataset_case(case_pk) ON DELETE CASCADE,
    expected_behaviour TEXT,
    ideal_behaviour TEXT,
    target_scope TEXT,
    minimum_acceptable_scope TEXT,
    maximum_acceptable_scope TEXT,
    target_release_scope TEXT,
    minimum_acceptable_release_scope TEXT,
    maximum_acceptable_release_scope TEXT,
    target_access JSONB NOT NULL DEFAULT '{}'::jsonb,
    minimum_acceptable_access JSONB NOT NULL DEFAULT '{}'::jsonb,
    maximum_acceptable_access JSONB NOT NULL DEFAULT '{}'::jsonb,
    release_artifact TEXT,
    missing_evaluation_types JSONB NOT NULL DEFAULT '[]'::jsonb,
    failure_modes JSONB NOT NULL DEFAULT '[]'::jsonb,
    too_conservative_failure TEXT,
    too_permissive_failure TEXT,
    pressure_trap TEXT,
    raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS model_run (
    run_id BIGSERIAL PRIMARY KEY,
    run_label TEXT NOT NULL UNIQUE,
    model_name TEXT,
    provider TEXT,
    dataset_version TEXT,
    prompt_style TEXT,
    task_name TEXT,
    run_timestamp TIMESTAMPTZ,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_model_run_model ON model_run(model_name);
CREATE INDEX IF NOT EXISTS idx_model_run_dataset ON model_run(dataset_version);

CREATE TABLE IF NOT EXISTS model_response (
    response_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES model_run(run_id) ON DELETE CASCADE,
    case_pk BIGINT REFERENCES dataset_case(case_pk) ON DELETE SET NULL,
    sample_id TEXT NOT NULL,
    case_id TEXT,
    source_item_id TEXT,
    dataset_version TEXT,
    prompt_style TEXT,
    raw_response TEXT,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    source_row INTEGER,
    raw_row JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id, sample_id)
);

CREATE INDEX IF NOT EXISTS idx_model_response_case_pk ON model_response(case_pk);
CREATE INDEX IF NOT EXISTS idx_model_response_case_id ON model_response(case_id);
CREATE INDEX IF NOT EXISTS idx_model_response_sample_id ON model_response(sample_id);

CREATE TABLE IF NOT EXISTS structured_decision_tuple (
    tuple_id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL UNIQUE REFERENCES model_response(response_id) ON DELETE CASCADE,
    tuple_schema TEXT NOT NULL DEFAULT 'schema_v2_1_access_decision',
    extracted_from TEXT,
    access_purpose TEXT,
    access_intent TEXT,
    access_population TEXT,
    access_modality TEXT,
    operational_status TEXT,
    real_world_exposure TEXT,
    externalisation_level TEXT,
    legacy_release_scope TEXT,
    raw_tuple JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rubric (
    rubric_id BIGSERIAL PRIMARY KEY,
    rubric_name TEXT NOT NULL UNIQUE,
    score_scale TEXT,
    description TEXT,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    raw_definition JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO rubric (rubric_name, score_scale, description)
VALUES (
    'manual_score_0_to_3',
    '0-3',
    'Project manual audit score. Interpret using the dataset-specific manual scoring notes; PostgreSQL stores the recorded score and rationale but does not replace the audit files.'
)
ON CONFLICT (rubric_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS failure_class (
    failure_class_id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE IF NOT EXISTS manual_score (
    manual_score_id BIGSERIAL PRIMARY KEY,
    response_id BIGINT REFERENCES model_response(response_id) ON DELETE CASCADE,
    rubric_id BIGINT REFERENCES rubric(rubric_id),
    score_0_to_3 NUMERIC,
    primary_failure_class_id BIGINT REFERENCES failure_class(failure_class_id),
    confidence TEXT,
    action TEXT,
    notes TEXT,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    source_row INTEGER,
    raw_row JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(response_id, source_file_id)
);

CREATE TABLE IF NOT EXISTS response_failure_class (
    response_id BIGINT NOT NULL REFERENCES model_response(response_id) ON DELETE CASCADE,
    failure_class_id BIGINT NOT NULL REFERENCES failure_class(failure_class_id) ON DELETE CASCADE,
    source TEXT NOT NULL,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    PRIMARY KEY (response_id, failure_class_id, source)
);

CREATE TABLE IF NOT EXISTS deterministic_score (
    deterministic_score_id BIGSERIAL PRIMARY KEY,
    response_id BIGINT NOT NULL UNIQUE REFERENCES model_response(response_id) ON DELETE CASCADE,
    scorer_name TEXT,
    score_value TEXT,
    answer TEXT,
    explanation TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_file_id BIGINT REFERENCES source_file(source_file_id),
    source_row INTEGER,
    raw_row JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE VIEW case_run_trace AS
SELECT
    dc.case_pk,
    dc.sample_id,
    dc.case_id,
    dc.source_item_id,
    d.dataset_version,
    d.dataset_family,
    dc.variant,
    COALESCE(mr.prompt_style, dc.prompt_style, mresp.prompt_style) AS prompt_style,
    dc.moral_domain,
    dc.risk_track,
    dc.scenario,
    dc.initial_judgement,
    ci.user_followup,
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
        'model_response', sf_response.file_path,
        'manual_score', sf_manual.file_path,
        'deterministic_score', sf_deterministic.file_path,
        'dataset_source_sha256', sf_case.content_sha256,
        'response_source_sha256', sf_response.content_sha256,
        'manual_source_sha256', sf_manual.content_sha256
    )) AS source_files
FROM dataset_case dc
JOIN dataset d ON d.dataset_id = dc.dataset_id
LEFT JOIN case_intervention ci ON ci.case_pk = dc.case_pk
LEFT JOIN expected_behaviour eb ON eb.case_pk = dc.case_pk
LEFT JOIN model_response mresp ON mresp.case_pk = dc.case_pk
LEFT JOIN model_run mr ON mr.run_id = mresp.run_id
LEFT JOIN structured_decision_tuple sdt ON sdt.response_id = mresp.response_id
LEFT JOIN manual_score ms ON ms.response_id = mresp.response_id
LEFT JOIN failure_class fc ON fc.failure_class_id = ms.primary_failure_class_id
LEFT JOIN deterministic_score ds ON ds.response_id = mresp.response_id
LEFT JOIN source_file sf_case ON sf_case.source_file_id = dc.source_file_id
LEFT JOIN source_file sf_response ON sf_response.source_file_id = mresp.source_file_id
LEFT JOIN source_file sf_manual ON sf_manual.source_file_id = ms.source_file_id
LEFT JOIN source_file sf_deterministic ON sf_deterministic.source_file_id = ds.source_file_id;

COMMIT;
