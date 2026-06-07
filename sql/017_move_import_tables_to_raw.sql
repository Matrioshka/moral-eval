-- Move imported/source-shaped provenance tables from public to raw.
--
-- This migration does not move operational tables and does not create public
-- compatibility views for raw/import tables. Public compatibility aliases are
-- intentionally avoided for writable ingest tables.
--
-- Apply sql/018_create_rpt_reporting_views_from_raw.sql after this migration to
-- recreate reporting views against raw.* table names.

BEGIN;

CREATE SCHEMA IF NOT EXISTS raw;

DO $$
DECLARE
    table_name text;
    import_tables text[] := ARRAY[
        'source_file',
        'dataset',
        'dataset_case',
        'case_intervention',
        'expected_behaviour',
        'model_run',
        'model_response',
        'manual_score',
        'deterministic_score',
        'structured_decision_tuple',
        'response_failure_class'
    ];
BEGIN
    FOREACH table_name IN ARRAY import_tables LOOP
        IF to_regclass(format('public.%I', table_name)) IS NOT NULL THEN
            IF to_regclass(format('raw.%I', table_name)) IS NOT NULL THEN
                RAISE EXCEPTION 'Cannot move %. Both public.% and raw.% exist.', table_name, table_name, table_name;
            END IF;

            EXECUTE format('ALTER TABLE public.%I SET SCHEMA raw', table_name);
            RAISE NOTICE 'Moved public.% to raw.%', table_name, table_name;
        ELSIF to_regclass(format('raw.%I', table_name)) IS NOT NULL THEN
            RAISE NOTICE 'raw.% already exists; public.% is absent, skipping', table_name, table_name;
        ELSE
            RAISE EXCEPTION 'Expected import table %. It exists in neither public nor raw.', table_name;
        END IF;
    END LOOP;
END $$;

COMMIT;
