-- Promote curated dataset and run concepts into the public operational schema.
--
-- raw.dataset and raw.model_run remain source-shaped provenance tables.
-- public.dataset and public.run are the curated operational dimensions used by
-- eval_case, response, and reporting views.

BEGIN;

CREATE TABLE IF NOT EXISTS public.dataset (
    dataset_id bigint PRIMARY KEY,
    dataset_version text NOT NULL UNIQUE,
    dataset_family text,
    source_file_id bigint REFERENCES raw.source_file(source_file_id),
    raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.run (
    run_id bigint PRIMARY KEY,
    run_label text NOT NULL UNIQUE,
    model_name text,
    provider text,
    dataset_id bigint REFERENCES public.dataset(dataset_id),
    dataset_version text,
    prompt_style text,
    run_timestamp timestamptz,
    source_file_id bigint REFERENCES raw.source_file(source_file_id),
    raw_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_public_dataset_dataset_family
    ON public.dataset(dataset_family);

CREATE INDEX IF NOT EXISTS ix_public_run_model_name
    ON public.run(model_name);

CREATE INDEX IF NOT EXISTS ix_public_run_dataset_id
    ON public.run(dataset_id);

INSERT INTO public.dataset AS dataset (
    dataset_id,
    dataset_version,
    dataset_family,
    source_file_id,
    raw_metadata,
    created_at,
    updated_at
)
SELECT
    d.dataset_id,
    d.dataset_version,
    d.dataset_family,
    d.source_file_id,
    COALESCE(d.raw_metadata, '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
        'legacy_table', 'raw.dataset',
        'promotion_migration', '019'
    )),
    d.first_seen_at,
    d.updated_at
FROM raw.dataset d
ON CONFLICT (dataset_id) DO UPDATE SET
    dataset_version = EXCLUDED.dataset_version,
    dataset_family = EXCLUDED.dataset_family,
    source_file_id = EXCLUDED.source_file_id,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

INSERT INTO public.run AS run (
    run_id,
    run_label,
    model_name,
    provider,
    dataset_id,
    dataset_version,
    prompt_style,
    run_timestamp,
    source_file_id,
    raw_metadata,
    created_at,
    updated_at
)
SELECT
    mr.run_id,
    mr.run_label,
    mr.model_name,
    mr.provider,
    d.dataset_id,
    mr.dataset_version,
    mr.prompt_style,
    mr.run_timestamp,
    mr.source_file_id,
    COALESCE(mr.raw_metadata, '{}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
        'legacy_table', 'raw.model_run',
        'promotion_migration', '019'
    )),
    mr.created_at,
    mr.updated_at
FROM raw.model_run mr
LEFT JOIN public.dataset d
    ON d.dataset_version = mr.dataset_version
ON CONFLICT (run_id) DO UPDATE SET
    run_label = EXCLUDED.run_label,
    model_name = EXCLUDED.model_name,
    provider = EXCLUDED.provider,
    dataset_id = EXCLUDED.dataset_id,
    dataset_version = EXCLUDED.dataset_version,
    prompt_style = EXCLUDED.prompt_style,
    run_timestamp = EXCLUDED.run_timestamp,
    source_file_id = EXCLUDED.source_file_id,
    raw_metadata = EXCLUDED.raw_metadata,
    updated_at = now();

-- Drop existing operational FKs that still point at raw provenance tables.
DO $$
DECLARE
    constraint_name text;
BEGIN
    FOR constraint_name IN
        SELECT con.conname
        FROM pg_constraint con
        WHERE con.conrelid = 'public.eval_case'::regclass
          AND con.confrelid = 'raw.dataset'::regclass
          AND con.contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE public.eval_case DROP CONSTRAINT %I', constraint_name);
    END LOOP;

    FOR constraint_name IN
        SELECT con.conname
        FROM pg_constraint con
        WHERE con.conrelid = 'public.response'::regclass
          AND con.confrelid = 'raw.model_run'::regclass
          AND con.contype = 'f'
    LOOP
        EXECUTE format('ALTER TABLE public.response DROP CONSTRAINT %I', constraint_name);
    END LOOP;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conrelid = 'public.eval_case'::regclass
          AND con.conname = 'eval_case_dataset_id_fkey'
    ) THEN
        ALTER TABLE public.eval_case
            ADD CONSTRAINT eval_case_dataset_id_fkey
            FOREIGN KEY (dataset_id)
            REFERENCES public.dataset(dataset_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint con
        WHERE con.conrelid = 'public.response'::regclass
          AND con.conname = 'response_run_id_fkey'
    ) THEN
        ALTER TABLE public.response
            ADD CONSTRAINT response_run_id_fkey
            FOREIGN KEY (run_id)
            REFERENCES public.run(run_id);
    END IF;
END $$;

COMMENT ON TABLE public.dataset IS
    'Curated operational dataset dimension promoted from raw.dataset. Raw remains source-shaped provenance.';

COMMENT ON TABLE public.run IS
    'Curated operational model/eval run dimension promoted from raw.model_run. Raw remains source-shaped provenance.';

COMMIT;
