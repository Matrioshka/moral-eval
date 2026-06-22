-- Operational control state for manifest-driven dataset generation.
--
-- JSONL and CSV files remain the payload and audit artefacts. These tables
-- record intended runs, planned stages, current artefact observations, and
-- human gate state.

BEGIN;

CREATE TABLE IF NOT EXISTS public.dataset_generation_run (
    dataset_generation_run_id bigserial PRIMARY KEY,
    run_slug text NOT NULL UNIQUE,
    name text NOT NULL,
    schema_version text NOT NULL,
    status text NOT NULL DEFAULT 'initialised'
        CHECK (status IN ('initialised', 'running', 'waiting_human', 'completed', 'failed')),
    current_stage text,
    output_dir text NOT NULL,
    manifest_path text NOT NULL,
    manifest_sha256 text NOT NULL,
    manifest_snapshot jsonb NOT NULL,
    git_commit text,
    last_error text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.dataset_generation_stage (
    dataset_generation_stage_id bigserial PRIMARY KEY,
    dataset_generation_run_id bigint NOT NULL
        REFERENCES public.dataset_generation_run(dataset_generation_run_id) ON DELETE CASCADE,
    stage_key text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 1),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
    stage_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    stage_result jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text,
    started_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_generation_run_id, stage_key)
);

CREATE TABLE IF NOT EXISTS public.dataset_generation_artifact (
    dataset_generation_artifact_id bigserial PRIMARY KEY,
    dataset_generation_run_id bigint NOT NULL
        REFERENCES public.dataset_generation_run(dataset_generation_run_id) ON DELETE CASCADE,
    producing_stage_id bigint
        REFERENCES public.dataset_generation_stage(dataset_generation_stage_id) ON DELETE SET NULL,
    artifact_key text NOT NULL,
    artifact_path text NOT NULL,
    artifact_type text NOT NULL,
    artifact_role text NOT NULL,
    sha256 text,
    byte_count bigint CHECK (byte_count IS NULL OR byte_count >= 0),
    row_count bigint CHECK (row_count IS NULL OR row_count >= 0),
    human_edited boolean NOT NULL DEFAULT false,
    artifact_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    observed_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_generation_run_id, artifact_key)
);

CREATE TABLE IF NOT EXISTS public.dataset_generation_gate (
    dataset_generation_gate_id bigserial PRIMARY KEY,
    dataset_generation_run_id bigint NOT NULL
        REFERENCES public.dataset_generation_run(dataset_generation_run_id) ON DELETE CASCADE,
    dataset_generation_stage_id bigint
        REFERENCES public.dataset_generation_stage(dataset_generation_stage_id) ON DELETE SET NULL,
    template_artifact_id bigint
        REFERENCES public.dataset_generation_artifact(dataset_generation_artifact_id) ON DELETE SET NULL,
    completed_artifact_id bigint
        REFERENCES public.dataset_generation_artifact(dataset_generation_artifact_id) ON DELETE SET NULL,
    gate_key text NOT NULL,
    gate_type text NOT NULL,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'satisfied', 'failed', 'waived')),
    expected_completed_path text,
    instructions text,
    validation_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    opened_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dataset_generation_run_id, gate_key)
);

CREATE INDEX IF NOT EXISTS ix_dataset_generation_run_status
    ON public.dataset_generation_run(status);

CREATE INDEX IF NOT EXISTS ix_dataset_generation_stage_run_status
    ON public.dataset_generation_stage(dataset_generation_run_id, status, ordinal);

CREATE INDEX IF NOT EXISTS ix_dataset_generation_artifact_run
    ON public.dataset_generation_artifact(dataset_generation_run_id);

CREATE INDEX IF NOT EXISTS ix_dataset_generation_gate_run_status
    ON public.dataset_generation_gate(dataset_generation_run_id, status);

COMMENT ON TABLE public.dataset_generation_run IS
    'Manifest snapshot and current control state for one dataset-generation run.';

COMMENT ON TABLE public.dataset_generation_stage IS
    'Planned and observed stage state for a dataset-generation run; this migration does not execute stages.';

COMMENT ON TABLE public.dataset_generation_artifact IS
    'Current file artefact observation for a dataset-generation run. Payload bytes remain on disk.';

COMMENT ON TABLE public.dataset_generation_gate IS
    'Human gate state and validation metadata for a dataset-generation run.';

COMMIT;
