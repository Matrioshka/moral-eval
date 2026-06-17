-- Passive per-model-call diagnostics for Inspect/provider usage metadata.
--
-- Rows are created only from confirmed distinct raw model-call/event usage
-- payloads. Aggregate sample usage remains in public.response_diagnostic.

CREATE SCHEMA IF NOT EXISTS rpt;

CREATE TABLE IF NOT EXISTS public.model_call_diagnostic (
    model_call_diagnostic_id bigserial PRIMARY KEY,
    experiment_pipeline_run_id bigint NOT NULL REFERENCES public.experiment_pipeline_run(experiment_pipeline_run_id),
    inspect_log_sample_id bigint NOT NULL REFERENCES public.inspect_log_sample(inspect_log_sample_id),
    response_id bigint NULL REFERENCES public.response(response_id),
    eval_case_id bigint NULL REFERENCES public.eval_case(eval_case_id),
    case_turn_id bigint NULL REFERENCES public.case_turn(case_turn_id),
    diagnostic_version text NOT NULL DEFAULT 'v1',
    diagnostic_mode text NOT NULL DEFAULT 'usage_only',
    source_scope text NOT NULL,
    source_event_index integer NOT NULL DEFAULT -1,
    model_call_index integer NOT NULL DEFAULT -1,
    turn_index integer NULL,
    turn_label text NULL,
    link_confidence text NOT NULL DEFAULT 'unverified',
    link_method text NULL,
    input_tokens integer NULL,
    output_tokens integer NULL,
    total_tokens integer NULL,
    reasoning_tokens integer NULL,
    thinking_tokens integer NULL,
    cached_input_tokens integer NULL,
    raw_usage_json jsonb NULL,
    raw_event_json jsonb NULL,
    raw_provider_metadata_json jsonb NULL,
    headline_eligible boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.model_call_diagnostic
    ALTER COLUMN experiment_pipeline_run_id SET NOT NULL,
    ALTER COLUMN inspect_log_sample_id SET NOT NULL,
    ADD COLUMN IF NOT EXISTS source_event_index integer NOT NULL DEFAULT -1,
    ADD COLUMN IF NOT EXISTS model_call_index integer NOT NULL DEFAULT -1;

CREATE UNIQUE INDEX IF NOT EXISTS ux_model_call_diagnostic_source
    ON public.model_call_diagnostic (
        experiment_pipeline_run_id,
        inspect_log_sample_id,
        diagnostic_version,
        diagnostic_mode,
        source_scope,
        source_event_index,
        model_call_index
    );

CREATE INDEX IF NOT EXISTS ix_model_call_diagnostic_pipeline_run
    ON public.model_call_diagnostic(experiment_pipeline_run_id);

CREATE INDEX IF NOT EXISTS ix_model_call_diagnostic_inspect_sample
    ON public.model_call_diagnostic(inspect_log_sample_id);

CREATE INDEX IF NOT EXISTS ix_model_call_diagnostic_response
    ON public.model_call_diagnostic(response_id);

CREATE INDEX IF NOT EXISTS ix_model_call_diagnostic_eval_case
    ON public.model_call_diagnostic(eval_case_id);

CREATE INDEX IF NOT EXISTS ix_model_call_diagnostic_case_turn
    ON public.model_call_diagnostic(case_turn_id);

CREATE INDEX IF NOT EXISTS ix_model_call_diagnostic_link_confidence
    ON public.model_call_diagnostic(link_confidence);

CREATE INDEX IF NOT EXISTS ix_model_call_diagnostic_headline_eligible
    ON public.model_call_diagnostic(headline_eligible);

DROP VIEW IF EXISTS rpt.model_call_diagnostics_by_sample;
DROP VIEW IF EXISTS rpt.model_call_diagnostics;

CREATE VIEW rpt.model_call_diagnostics AS
SELECT
    model_call_diagnostic_id,
    experiment_pipeline_run_id,
    inspect_log_sample_id,
    response_id,
    eval_case_id,
    case_turn_id,
    diagnostic_version,
    diagnostic_mode,
    source_scope,
    source_event_index,
    model_call_index,
    turn_index,
    turn_label,
    link_confidence,
    link_method,
    input_tokens,
    output_tokens,
    total_tokens,
    reasoning_tokens,
    thinking_tokens,
    cached_input_tokens,
    headline_eligible,
    created_at
FROM public.model_call_diagnostic;

CREATE VIEW rpt.model_call_diagnostics_by_sample AS
SELECT
    mcd.experiment_pipeline_run_id,
    mcd.inspect_log_sample_id,
    ils.sample_id,
    COALESCE(ils.dataset_version, epr.dataset_version) AS dataset_version,
    COALESCE(epr.answer_model->>'model', epr.answer_model->>'name', epr.answer_model->>'id') AS model_name,
    epr.task,
    count(*)::integer AS model_call_count,
    count(*) FILTER (WHERE mcd.link_confidence = 'exact')::integer AS exact_linked_count,
    count(*) FILTER (WHERE mcd.link_confidence = 'unverified')::integer AS unverified_count,
    COALESCE(sum(mcd.input_tokens), 0)::integer AS total_input_tokens_provider_reported,
    COALESCE(sum(mcd.output_tokens), 0)::integer AS total_output_tokens_provider_reported,
    COALESCE(sum(mcd.reasoning_tokens), 0)::integer AS total_reasoning_tokens_provider_reported,
    COALESCE(sum(mcd.thinking_tokens), 0)::integer AS total_thinking_tokens_provider_reported,
    COALESCE(sum(mcd.total_tokens), 0)::integer AS total_provider_reported_total_tokens,
    bool_and(mcd.headline_eligible) AS all_headline_eligible,
    bool_or(mcd.link_confidence = 'unverified') AS has_unverified_linkage,
    min(mcd.model_call_index) AS min_model_call_index,
    max(mcd.model_call_index) AS max_model_call_index,
    min(mcd.source_event_index) AS min_source_event_index,
    max(mcd.source_event_index) AS max_source_event_index
FROM public.model_call_diagnostic mcd
JOIN public.inspect_log_sample ils
  ON ils.inspect_log_sample_id = mcd.inspect_log_sample_id
JOIN public.experiment_pipeline_run epr
  ON epr.experiment_pipeline_run_id = mcd.experiment_pipeline_run_id
GROUP BY
    mcd.experiment_pipeline_run_id,
    mcd.inspect_log_sample_id,
    ils.sample_id,
    COALESCE(ils.dataset_version, epr.dataset_version),
    COALESCE(epr.answer_model->>'model', epr.answer_model->>'name', epr.answer_model->>'id'),
    epr.task;

COMMENT ON TABLE public.model_call_diagnostic IS
    'Passive per-model-call diagnostics extracted only from confirmed distinct raw Inspect/provider call usage payloads.';

COMMENT ON VIEW rpt.model_call_diagnostics IS
    'Reporting view over passive per-model-call diagnostics. Raw payload JSON is intentionally omitted.';

COMMENT ON VIEW rpt.model_call_diagnostics_by_sample IS
    'One row per Inspect sample summarising passive per-model-call diagnostics with provider-reported token metadata and linkage status counts.';
