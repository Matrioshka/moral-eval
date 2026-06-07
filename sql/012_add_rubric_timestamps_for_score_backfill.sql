-- Add timestamp columns expected by score backfill migrations.
--
-- Some earlier provenance schemas created rubric without created_at/updated_at,
-- while later operational migrations update rubric.updated_at during idempotent
-- upserts. This small compatibility migration makes rubric match the timestamp
-- convention used by the newer lookup/operational tables.

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

ALTER TABLE rubric
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE rubric
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

DROP TRIGGER IF EXISTS trg_rubric_set_updated_at ON rubric;
CREATE TRIGGER trg_rubric_set_updated_at
BEFORE UPDATE ON rubric
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

COMMIT;
