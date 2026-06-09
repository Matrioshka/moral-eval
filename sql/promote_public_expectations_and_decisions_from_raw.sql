-- Promote case expectations and structured response decisions into public.
--
-- raw.expected_behaviour and raw.structured_decision_tuple remain provenance.
-- public.case_expectation and public.response_structured_decision are the
-- operational forms used by reporting, scoring, and app queries.

BEGIN;

CREATE TABLE IF NOT EXISTS public.case_expectation (
    case_expectation_id bigserial PRIMARY KEY,
    eval_case_id bigint NOT NULL UNIQUE REFERENCES public.eval_case(eval_case_id) ON DELETE CASCADE,
    expected_behaviour text,
    ideal_behaviour text,
    target_scope text,
    minimum_acceptable_scope text,
    maximum_acceptable_scope text,
    target_release_scope text,
    minimum_acceptable_release_scope text,
    maximum_acceptable_release_scope text,
    target_access jsonb NOT NULL DEFAULT '{}'::jsonb,
    minimum_acceptable_access jsonb NOT NULL DEFAULT '{}'::jsonb,
    maximum_acceptable_access jsonb NOT NULL DEFAULT '{}'::jsonb,
    release_artifact text,
    missing_evaluation_types jsonb NOT NULL DEFAULT '[]'::jsonb,
    failure_modes jsonb NOT NULL DEFAULT '[]'::jsonb,
    too_conservative_failure text,
    too_permissive_failure text,
    pressure_trap text,
    source_case_pk bigint,
    source_file_id bigint REFERENCES raw.source_file(source_file_id),
    legacy_expected_behaviour_id bigint UNIQUE,
    raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_case_expectation_eval_case_id
    ON public.case_expectation(eval_case_id);

CREATE INDEX IF NOT EXISTS ix_case_expectation_release_artifact
    ON public.case_expectation(release_artifact);

CREATE TABLE IF NOT EXISTS public.response_structured_decision (
    response_structured_decision_id bigserial PRIMARY KEY,
    response_id bigint NOT NULL UNIQUE REFERENCES public.response(response_id) ON DELETE CASCADE,
    tuple_schema text NOT NULL DEFAULT 'schema_v2_1_access_decision',
    extracted_from text,
    access_purpose text,
    access_intent text,
    access_population text,
    access_modality text,
    operational_status text,
    real_world_exposure text,
    externalisation_level text,
    legacy_release_scope text,
    raw_tuple jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_file_id bigint REFERENCES raw.source_file(source_file_id),
    legacy_structured_decision_tuple_id bigint UNIQUE,
    raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_response_structured_decision_response_id
    ON public.response_structured_decision(response_id);

CREATE INDEX IF NOT EXISTS ix_response_structured_decision_access_intent
    ON public.response_structured_decision(access_intent);

CREATE INDEX IF NOT EXISTS ix_response_structured_decision_access_modality
    ON public.response_structured_decision(access_modality);

INSERT INTO public.case_expectation AS case_expectation (
    eval_case_id,
    expected_behaviour,
    ideal_behaviour,
    target_scope,
    minimum_acceptable_scope,
    maximum_acceptable_scope,
    target_release_scope,
    minimum_acceptable_release_scope,
    maximum_acceptable_release_scope,
    target_access,
    minimum_acceptable_access,
    maximum_acceptable_access,
    release_artifact,
    missing_evaluation_types,
    failure_modes,
    too_conservative_failure,
    too_permissive_failure,
    pressure_trap,
    source_case_pk,
    source_file_id,
    legacy_expected_behaviour_id,
    raw_metadata,
    created_at,
    updated_at
)
SELECT
    ec.eval_case_id,
    eb.expected_behaviour,
    eb.ideal_behaviour,
    eb.target_scope,
    eb.minimum_acceptable_scope,
    eb.maximum_acceptable_scope,
    eb.target_release_scope,
    eb.minimum_acceptable_release_scope,
    eb.maximum_acceptable_release_scope,
    COALESCE(eb.target_access, '{}'::jsonb),
    COALESCE(eb.minimum_acceptable_access, '{}'::jsonb),
    COALESCE(eb.maximum_acceptable_access, '{}'::jsonb),
    eb.release_artifact,
    COALESCE(eb.missing_evaluation_types, '[]'::jsonb),
    COALESCE(eb.failure_modes, '[]'::jsonb),
    eb.too_conservative_failure,
    eb.too_permissive_failure,
    eb.pressure_trap,
    eb.case_pk,
    ec.source_file_id,
    eb.expected_behaviour_id,
    COALESCE(eb.raw_metadata, '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
        'legacy_table', 'raw.expected_behaviour',
        'promotion_step', 'promote_public_expectations_and_decisions_from_raw',
        'source_file_note', 'raw.expected_behaviour has no source_file_id; inherited from public.eval_case.source_file_id'
    )),
    now(),
    now()
FROM raw.expected_behaviour eb
JOIN public.eval_case ec
    ON ec.dataset_case_pk = eb.case_pk
ON CONFLICT (eval_case_id) DO UPDATE SET
    expected_behaviour = EXCLUDED.expected_behaviour,
    ideal_behaviour = EXCLUDED.ideal_behaviour,
    target_scope = EXCLUDED.target_scope,
    minimum_acceptable_scope = EXCLUDED.minimum_acceptable_scope,
    maximum_acceptable_scope = EXCLUDED.maximum_acceptable_scope,
    target_release_scope = EXCLUDED.target_release_scope,
    minimum_acceptable_release_scope = EXCLUDED.minimum_acceptable_release_scope,
    maximum_acceptable_release_scope = EXCLUDED.maximum_acceptable_release_scope,
    target_access = EXCLUDED.target_access,
    minimum_acceptable_access = EXCLUDED.minimum_acceptable_access,
    maximum_acceptable_access = EXCLUDED.maximum_acceptable_access,
    release_artifact = EXCLUDED.release_artifact,
    missing_evaluation_types = EXCLUDED.missing_evaluation_types,
    failure_modes = EXCLUDED.failure_modes,
    too_conservative_failure = EXCLUDED.too_conservative_failure,
    too_permissive_failure = EXCLUDED.too_permissive_failure,
    pressure_trap = EXCLUDED.pressure_trap,
    source_case_pk = EXCLUDED.source_case_pk,
    source_file_id = EXCLUDED.source_file_id,
    legacy_expected_behaviour_id = EXCLUDED.legacy_expected_behaviour_id,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

INSERT INTO public.response_structured_decision AS response_structured_decision (
    response_id,
    tuple_schema,
    extracted_from,
    access_purpose,
    access_intent,
    access_population,
    access_modality,
    operational_status,
    real_world_exposure,
    externalisation_level,
    legacy_release_scope,
    raw_tuple,
    source_file_id,
    legacy_structured_decision_tuple_id,
    raw_metadata,
    created_at,
    updated_at
)
SELECT
    r.response_id,
    COALESCE(sdt.tuple_schema, 'schema_v2_1_access_decision'),
    sdt.extracted_from,
    sdt.access_purpose,
    sdt.access_intent,
    sdt.access_population,
    sdt.access_modality,
    sdt.operational_status,
    sdt.real_world_exposure,
    sdt.externalisation_level,
    sdt.legacy_release_scope,
    COALESCE(sdt.raw_tuple, '{}'::jsonb),
    sdt.source_file_id,
    sdt.tuple_id,
    jsonb_strip_nulls(jsonb_build_object(
        'legacy_table', 'raw.structured_decision_tuple',
        'promotion_step', 'promote_public_expectations_and_decisions_from_raw'
    )),
    COALESCE(sdt.created_at, now()),
    COALESCE(sdt.updated_at, now())
FROM raw.structured_decision_tuple sdt
JOIN public.response r
    ON r.legacy_model_response_id = sdt.response_id
ON CONFLICT (response_id) DO UPDATE SET
    tuple_schema = EXCLUDED.tuple_schema,
    extracted_from = EXCLUDED.extracted_from,
    access_purpose = EXCLUDED.access_purpose,
    access_intent = EXCLUDED.access_intent,
    access_population = EXCLUDED.access_population,
    access_modality = EXCLUDED.access_modality,
    operational_status = EXCLUDED.operational_status,
    real_world_exposure = EXCLUDED.real_world_exposure,
    externalisation_level = EXCLUDED.externalisation_level,
    legacy_release_scope = EXCLUDED.legacy_release_scope,
    raw_tuple = EXCLUDED.raw_tuple,
    source_file_id = EXCLUDED.source_file_id,
    legacy_structured_decision_tuple_id = EXCLUDED.legacy_structured_decision_tuple_id,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

COMMENT ON TABLE public.case_expectation IS
    'Operational case-level expectation and acceptable-bound metadata promoted from raw.expected_behaviour.';

COMMENT ON TABLE public.response_structured_decision IS
    'Operational structured extraction attached to public.response, promoted from raw.structured_decision_tuple.';

COMMIT;
