-- Passive response diagnostics for Inspect/provider metadata.
--
-- This table stores already-produced usage/diagnostic metadata linked to the
-- operational response table. It does not affect eval prompts or scoring.

CREATE SCHEMA IF NOT EXISTS rpt;

CREATE TABLE IF NOT EXISTS public.response_diagnostic (
    response_diagnostic_id bigserial PRIMARY KEY,
    response_id bigint NOT NULL REFERENCES public.response(response_id),
    diagnostic_version text NOT NULL DEFAULT 'v1',
    diagnostic_mode text NOT NULL,
    headline_eligible boolean NOT NULL DEFAULT true,
    visible_to_model_next_turn boolean NOT NULL DEFAULT false,
    reasoning_requested boolean NOT NULL DEFAULT false,
    reasoning_summary_requested boolean NOT NULL DEFAULT false,
    reasoning_effort text NULL,
    input_tokens integer NULL,
    output_tokens integer NULL,
    total_tokens integer NULL,
    reasoning_tokens integer NULL,
    thinking_tokens integer NULL,
    cached_input_tokens integer NULL,
    reasoning_summary_text text NULL,
    raw_usage_json jsonb NULL,
    raw_reasoning_summary_json jsonb NULL,
    raw_provider_metadata_json jsonb NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.response_diagnostic
    ADD COLUMN IF NOT EXISTS diagnostic_version text NOT NULL DEFAULT 'v1';

CREATE UNIQUE INDEX IF NOT EXISTS ux_response_diagnostic_response_mode_version
    ON public.response_diagnostic(response_id, diagnostic_mode, diagnostic_version);

CREATE INDEX IF NOT EXISTS ix_response_diagnostic_response_id
    ON public.response_diagnostic(response_id);

CREATE INDEX IF NOT EXISTS ix_response_diagnostic_mode
    ON public.response_diagnostic(diagnostic_mode);

CREATE INDEX IF NOT EXISTS ix_response_diagnostic_headline_eligible
    ON public.response_diagnostic(headline_eligible);

CREATE INDEX IF NOT EXISTS ix_response_diagnostic_reasoning_summary_requested
    ON public.response_diagnostic(reasoning_summary_requested);

DROP VIEW IF EXISTS rpt.response_diagnostics;

CREATE VIEW rpt.response_diagnostics AS
SELECT
    response_id,
    diagnostic_version,
    diagnostic_mode,
    headline_eligible,
    visible_to_model_next_turn,
    reasoning_requested,
    reasoning_summary_requested,
    reasoning_effort,
    input_tokens,
    output_tokens,
    total_tokens,
    reasoning_tokens,
    thinking_tokens,
    cached_input_tokens,
    created_at
FROM public.response_diagnostic;

COMMENT ON TABLE public.response_diagnostic IS
    'Passive response diagnostics linked to public.response. These metadata are not part of behavioural prompts or scoring.';

COMMENT ON VIEW rpt.response_diagnostics IS
    'Reporting view over passive response diagnostics, intentionally kept separate from main behavioural trace views.';
