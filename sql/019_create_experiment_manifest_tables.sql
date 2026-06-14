-- Operational experiment manifest and Inspect log provenance tables.
--
-- YAML remains a versionable local entry point, but these tables are the
-- operational store for manifests, pipeline runs, and raw Inspect sample traces.

BEGIN;

CREATE TABLE IF NOT EXISTS public.experiment_manifest (
    experiment_manifest_id bigserial PRIMARY KEY,
    experiment_slug text NOT NULL UNIQUE,
    name text,
    dataset_version text NOT NULL,
    dataset_alias text,
    dataset_file_path text,
    dataset_file_sha256 text,
    prompt_style text,
    task text NOT NULL,
    answer_models jsonb NOT NULL DEFAULT '[]'::jsonb,
    grader_models jsonb NOT NULL DEFAULT '[]'::jsonb,
    scoring jsonb NOT NULL DEFAULT '{}'::jsonb,
    exports jsonb NOT NULL DEFAULT '{}'::jsonb,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    limit_count integer,
    output_dir text,
    log_dir text,
    ingest boolean NOT NULL DEFAULT true,
    rebuild_derived boolean NOT NULL DEFAULT true,
    manifest_file_path text,
    manifest_file_sha256 text,
    raw_manifest jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.experiment_pipeline_run (
    experiment_pipeline_run_id bigserial PRIMARY KEY,
    experiment_manifest_id bigint REFERENCES public.experiment_manifest(experiment_manifest_id),
    operational_run_id bigint REFERENCES public.run(run_id),
    pipeline_run_key text NOT NULL UNIQUE,
    experiment_slug text NOT NULL,
    status text NOT NULL,
    task text,
    dataset_version text,
    dataset_alias text,
    prompt_style text,
    answer_model jsonb NOT NULL DEFAULT '{}'::jsonb,
    answer_models jsonb NOT NULL DEFAULT '[]'::jsonb,
    grader_models jsonb NOT NULL DEFAULT '[]'::jsonb,
    scoring jsonb NOT NULL DEFAULT '{}'::jsonb,
    exports jsonb NOT NULL DEFAULT '{}'::jsonb,
    provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    limit_count integer,
    output_dir text,
    inspect_command text,
    eval_log_path text,
    eval_log_sha256 text,
    original_eval_log_path text,
    outputs_csv_path text,
    outputs_csv_sha256 text,
    case_trace_csv_path text,
    case_trace_csv_row_count integer,
    run_summary_path text,
    run_summary jsonb,
    error text,
    raw_run_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.inspect_log_sample (
    inspect_log_sample_id bigserial PRIMARY KEY,
    experiment_pipeline_run_id bigint NOT NULL REFERENCES public.experiment_pipeline_run(experiment_pipeline_run_id) ON DELETE CASCADE,
    eval_case_id bigint REFERENCES public.eval_case(eval_case_id),
    response_id bigint REFERENCES public.response(response_id),
    sample_id text NOT NULL,
    dataset_version text,
    input_text text,
    target_text text,
    final_response text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    messages jsonb NOT NULL DEFAULT '[]'::jsonb,
    usage jsonb NOT NULL DEFAULT '{}'::jsonb,
    scores jsonb NOT NULL DEFAULT '{}'::jsonb,
    raw_sample jsonb NOT NULL,
    source_log_path text,
    source_log_sha256 text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (experiment_pipeline_run_id, sample_id)
);

ALTER TABLE public.experiment_pipeline_run
    ADD COLUMN IF NOT EXISTS operational_run_id bigint REFERENCES public.run(run_id);

ALTER TABLE public.inspect_log_sample
    ADD COLUMN IF NOT EXISTS eval_case_id bigint REFERENCES public.eval_case(eval_case_id),
    ADD COLUMN IF NOT EXISTS response_id bigint REFERENCES public.response(response_id);

CREATE INDEX IF NOT EXISTS ix_experiment_manifest_dataset_version
    ON public.experiment_manifest(dataset_version);

CREATE INDEX IF NOT EXISTS ix_experiment_pipeline_run_slug_status
    ON public.experiment_pipeline_run(experiment_slug, status);

CREATE INDEX IF NOT EXISTS ix_experiment_pipeline_run_operational_run
    ON public.experiment_pipeline_run(operational_run_id);

CREATE INDEX IF NOT EXISTS ix_inspect_log_sample_run
    ON public.inspect_log_sample(experiment_pipeline_run_id);

CREATE INDEX IF NOT EXISTS ix_inspect_log_sample_eval_case
    ON public.inspect_log_sample(eval_case_id);

CREATE INDEX IF NOT EXISTS ix_inspect_log_sample_response
    ON public.inspect_log_sample(response_id);

CREATE INDEX IF NOT EXISTS ix_inspect_log_sample_dataset_sample
    ON public.inspect_log_sample(dataset_version, sample_id);

COMMIT;
