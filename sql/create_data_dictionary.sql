-- Build a queryable data dictionary for raw, public, and rpt schemas.
--
-- This is deliberately derived from PostgreSQL's live catalog so it stays aligned
-- with the current database shape. Curated descriptions override generated
-- fallbacks for the project-specific operational spine.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpt;

CREATE TABLE IF NOT EXISTS public.data_dictionary (
    data_dictionary_id bigserial PRIMARY KEY,
    object_schema text NOT NULL,
    object_name text NOT NULL DEFAULT '',
    object_type text NOT NULL CHECK (object_type IN ('schema', 'table', 'view', 'column')),
    column_name text NOT NULL DEFAULT '',
    ordinal_position integer,
    data_type text,
    is_nullable boolean,
    column_default text,
    is_primary_key boolean NOT NULL DEFAULT false,
    is_foreign_key boolean NOT NULL DEFAULT false,
    references_schema text,
    references_table text,
    references_column text,
    semantic_group text NOT NULL DEFAULT 'unspecified',
    lifecycle_role text NOT NULL DEFAULT 'unspecified',
    description text NOT NULL,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (object_schema, object_name, object_type, column_name)
);

CREATE INDEX IF NOT EXISTS ix_data_dictionary_object
    ON public.data_dictionary(object_schema, object_name, object_type);

CREATE INDEX IF NOT EXISTS ix_data_dictionary_column
    ON public.data_dictionary(object_schema, object_name, column_name);

COMMENT ON TABLE public.data_dictionary IS
    'Queryable data dictionary for raw, public, and rpt schemas, generated from PostgreSQL catalog metadata plus curated project descriptions.';

DELETE FROM public.data_dictionary
WHERE object_schema IN ('raw', 'public', 'rpt');

WITH curated(object_schema, object_name, column_name, description, notes) AS (
    VALUES
        ('raw', '', '', 'Imported source-shaped provenance and landing schema. Raw tables preserve source artefact shape and lineage.', NULL),
        ('public', '', '', 'Operational schema for curated datasets, cases, turns, responses, scores, runs, lookup tables, and app-facing metadata.', NULL),
        ('rpt', '', '', 'Reporting schema for stable query surfaces and web-interface views over operational and provenance data.', NULL),

        ('public', 'dataset', '', 'Curated dataset/version dimension promoted from raw.dataset.', NULL),
        ('public', 'run', '', 'Model/eval run that produced responses. Distinct from experiment_pipeline_run orchestration executions.', NULL),
        ('public', 'eval_case', '', 'Curated evaluation case within a dataset.', NULL),
        ('public', 'case_turn', '', 'Prompt, scenario, or pressure/intervention turn belonging to an eval_case.', NULL),
        ('public', 'case_expectation', '', 'Operational expected behaviour, acceptable bounds, access tuple targets, and failure modes for an eval_case.', NULL),
        ('public', 'response', '', 'Model response to one case_turn in one run.', NULL),
        ('public', 'response_structured_decision', '', 'Operational structured extraction attached to a response, used for access/release-scope analysis.', 'Keep only fields that are scored, filtered, grouped, displayed, or needed for audit.'),
        ('public', 'score_event', '', 'Score, label, rationale, and failure-class judgement attached to a response.', NULL),
        ('public', 'data_dictionary', '', 'Queryable metadata describing schemas, tables, views, and columns.', NULL),

        ('rpt', 'run_summary', '', 'One row per public.run with response, case, structured-decision, and score counts for run-list pages.', NULL),
        ('rpt', 'run_detail', '', 'One row per response in a public.run, with case, turn, expectation, extraction, and score-rollup fields.', NULL),
        ('rpt', 'case_run_trace', '', 'Wide case/response/reporting trace view for case cards and diagnostics.', NULL),
        ('rpt', 'case_run_trace_reporting', '', 'Reporting-safe subset of case_run_trace excluding smoke, summary, expansion-candidate, and rewrite-candidate artefacts.', NULL),
        ('rpt', 'score_linkage_status', '', 'Diagnostic view showing whether raw manual scores link to operational score_event rows.', NULL),

        ('raw', 'source_file', '', 'Source artefact registry with path, kind, hash, size, record count, and ingestion timestamps.', NULL),
        ('raw', 'dataset_case', '', 'Imported dataset/case rows before operational normalisation into eval_case, scenario, case_turn, and case_expectation.', NULL),
        ('raw', 'case_intervention', '', 'Imported follow-up or intervention metadata before pressure-turn normalisation.', NULL),
        ('raw', 'expected_behaviour', '', 'Imported expected-behaviour metadata before promotion to public.case_expectation.', NULL),
        ('raw', 'model_response', '', 'Imported model response rows before promotion to public.response.', NULL),
        ('raw', 'structured_decision_tuple', '', 'Imported structured extraction rows before promotion to public.response_structured_decision.', NULL),
        ('raw', 'manual_score', '', 'Imported manual audit rows before promotion to public.score_event.', NULL),
        ('raw', 'deterministic_score', '', 'Imported deterministic scorer rows before promotion to public.score_event.', NULL),

        ('public', 'run', 'run_id', 'Primary key for a model/eval run.', NULL),
        ('public', 'run', 'run_label', 'Human-readable/source-derived identifier for a model/eval run.', NULL),
        ('public', 'run', 'model_name', 'Model name used for the run, when known or inferred.', NULL),
        ('public', 'run', 'dataset_id', 'Dataset declared for the run. Should match the dataset of responses through case_turn/eval_case.', NULL),
        ('public', 'run', 'run_timestamp', 'Timestamp associated with the model/eval run when known.', NULL),
        ('public', 'response', 'response_id', 'Primary key for an operational response.', NULL),
        ('public', 'response', 'case_turn_id', 'Case turn answered by this response.', NULL),
        ('public', 'response', 'run_id', 'Model/eval run that produced this response.', NULL),
        ('public', 'response', 'response_text', 'Model response text.', NULL),
        ('public', 'case_turn', 'turn_index', 'Order of the turn within its eval_case.', NULL),
        ('public', 'case_turn', 'turn_text', 'Text of the scenario, prompt, intervention, or pressure turn.', NULL),
        ('public', 'case_expectation', 'expected_behaviour', 'Concise expected behaviour or target judgement for the case.', NULL),
        ('public', 'case_expectation', 'target_access', 'Target access tuple for schema-v2.1 style release/access decisions.', NULL),
        ('public', 'case_expectation', 'maximum_acceptable_access', 'Most permissive acceptable access tuple before the response should be treated as over-expansive.', NULL),
        ('public', 'response_structured_decision', 'tuple_schema', 'Schema contract used to interpret the structured extraction row.', NULL),
        ('public', 'response_structured_decision', 'access_intent', 'Extracted intended use or access intent approved/implied by the response.', NULL),
        ('public', 'response_structured_decision', 'access_population', 'Extracted population or audience allowed by the response.', NULL),
        ('public', 'response_structured_decision', 'access_modality', 'Extracted access mode, channel, or deployment modality.', NULL),
        ('public', 'response_structured_decision', 'operational_status', 'Extracted operational/deployment status implied by the response.', NULL),
        ('public', 'response_structured_decision', 'real_world_exposure', 'Extracted degree of real-world exposure implied by the response.', NULL),
        ('public', 'response_structured_decision', 'externalisation_level', 'Extracted degree of external release or exposure implied by the response.', NULL),
        ('public', 'response_structured_decision', 'raw_tuple', 'Lossless structured extraction payload used to audit or recover fields beyond the promoted operational columns.', NULL),
        ('public', 'response_structured_decision', 'legacy_structured_decision_tuple_id', 'Lineage key back to raw.structured_decision_tuple.tuple_id.', NULL),
        ('public', 'score_event', 'score', 'Numeric score assigned by a manual, deterministic, or future scorer.', NULL),
        ('public', 'score_event', 'rationale', 'Reasoning or grading rationale for the score.', NULL)
), schema_rows AS (
    SELECT
        n.nspname AS object_schema,
        ''::text AS object_name,
        'schema'::text AS object_type,
        ''::text AS column_name,
        NULL::integer AS ordinal_position,
        NULL::text AS data_type,
        NULL::boolean AS is_nullable,
        NULL::text AS column_default,
        false AS is_primary_key,
        false AS is_foreign_key,
        NULL::text AS references_schema,
        NULL::text AS references_table,
        NULL::text AS references_column,
        CASE n.nspname
            WHEN 'raw' THEN 'provenance'
            WHEN 'public' THEN 'operational'
            WHEN 'rpt' THEN 'reporting'
            ELSE 'unspecified'
        END AS semantic_group,
        CASE n.nspname
            WHEN 'raw' THEN 'source_landing'
            WHEN 'public' THEN 'application_operational'
            WHEN 'rpt' THEN 'reporting_surface'
            ELSE 'unspecified'
        END AS lifecycle_role,
        COALESCE(curated.description, 'Schema ' || n.nspname || '.') AS description,
        curated.notes AS notes
    FROM pg_namespace n
    LEFT JOIN curated ON curated.object_schema = n.nspname AND curated.object_name = '' AND curated.column_name = ''
    WHERE n.nspname IN ('raw', 'public', 'rpt')
), object_rows AS (
    SELECT
        ns.nspname AS object_schema,
        cls.relname AS object_name,
        CASE cls.relkind
            WHEN 'r' THEN 'table'
            WHEN 'p' THEN 'table'
            WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'view'
            ELSE 'table'
        END AS object_type,
        ''::text AS column_name,
        NULL::integer AS ordinal_position,
        NULL::text AS data_type,
        NULL::boolean AS is_nullable,
        NULL::text AS column_default,
        false AS is_primary_key,
        false AS is_foreign_key,
        NULL::text AS references_schema,
        NULL::text AS references_table,
        NULL::text AS references_column,
        CASE
            WHEN ns.nspname = 'raw' THEN 'provenance'
            WHEN ns.nspname = 'rpt' THEN 'reporting'
            WHEN cls.relname IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'reference'
            WHEN cls.relname IN ('data_dictionary') THEN 'metadata'
            ELSE 'operational'
        END AS semantic_group,
        CASE
            WHEN ns.nspname = 'raw' THEN 'source_landing'
            WHEN ns.nspname = 'rpt' THEN 'reporting_surface'
            WHEN cls.relname IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'lookup'
            WHEN cls.relname IN ('data_dictionary') THEN 'metadata'
            ELSE 'application_operational'
        END AS lifecycle_role,
        COALESCE(curated.description, obj_description(cls.oid), initcap(replace(cls.relname, '_', ' ')) || ' ' || CASE cls.relkind WHEN 'v' THEN 'view' ELSE 'table' END || '.') AS description,
        curated.notes AS notes
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    LEFT JOIN curated ON curated.object_schema = ns.nspname AND curated.object_name = cls.relname AND curated.column_name = ''
    WHERE ns.nspname IN ('raw', 'public', 'rpt')
      AND cls.relkind IN ('r', 'p', 'v', 'm')
), column_catalog AS (
    SELECT
        ns.nspname AS object_schema,
        cls.relname AS object_name,
        CASE cls.relkind
            WHEN 'r' THEN 'table'
            WHEN 'p' THEN 'table'
            WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'view'
            ELSE 'table'
        END AS parent_object_type,
        att.attname AS column_name,
        att.attnum AS ordinal_position,
        format_type(att.atttypid, att.atttypmod) AS data_type,
        NOT att.attnotnull AS is_nullable,
        pg_get_expr(def.adbin, def.adrelid) AS column_default,
        col_description(cls.oid, att.attnum) AS catalog_description,
        cls.oid AS table_oid
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum > 0 AND NOT att.attisdropped
    LEFT JOIN pg_attrdef def ON def.adrelid = cls.oid AND def.adnum = att.attnum
    WHERE ns.nspname IN ('raw', 'public', 'rpt')
      AND cls.relkind IN ('r', 'p', 'v', 'm')
), primary_key_columns AS (
    SELECT
        ns.nspname AS object_schema,
        cls.relname AS object_name,
        att.attname AS column_name
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN unnest(con.conkey) AS key_attnum(attnum) ON true
    JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum = key_attnum.attnum
    WHERE con.contype = 'p'
), foreign_key_columns AS (
    SELECT
        ns.nspname AS object_schema,
        cls.relname AS object_name,
        att.attname AS column_name,
        ref_ns.nspname AS references_schema,
        ref_cls.relname AS references_table,
        ref_att.attname AS references_column
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN pg_class ref_cls ON ref_cls.oid = con.confrelid
    JOIN pg_namespace ref_ns ON ref_ns.oid = ref_cls.relnamespace
    JOIN unnest(con.conkey) WITH ORDINALITY AS key_col(attnum, ord) ON true
    JOIN unnest(con.confkey) WITH ORDINALITY AS ref_col(attnum, ord) ON ref_col.ord = key_col.ord
    JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum = key_col.attnum
    JOIN pg_attribute ref_att ON ref_att.attrelid = ref_cls.oid AND ref_att.attnum = ref_col.attnum
    WHERE con.contype = 'f'
), column_rows AS (
    SELECT
        col.object_schema,
        col.object_name,
        'column'::text AS object_type,
        col.column_name,
        col.ordinal_position,
        col.data_type,
        col.is_nullable,
        col.column_default,
        (pk.column_name IS NOT NULL) AS is_primary_key,
        (fk.column_name IS NOT NULL) AS is_foreign_key,
        fk.references_schema,
        fk.references_table,
        fk.references_column,
        CASE
            WHEN col.object_schema = 'raw' THEN 'provenance'
            WHEN col.object_schema = 'rpt' THEN 'reporting'
            WHEN col.object_name IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'reference'
            WHEN col.object_name IN ('data_dictionary') THEN 'metadata'
            ELSE 'operational'
        END AS semantic_group,
        CASE
            WHEN col.object_schema = 'raw' THEN 'source_landing_column'
            WHEN col.object_schema = 'rpt' THEN 'reporting_column'
            WHEN col.object_name IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'lookup_column'
            WHEN col.object_name IN ('data_dictionary') THEN 'metadata_column'
            ELSE 'application_operational_column'
        END AS lifecycle_role,
        COALESCE(curated.description, col.catalog_description, initcap(replace(col.column_name, '_', ' ')) || ' column on ' || col.object_schema || '.' || col.object_name || '.') AS description,
        curated.notes AS notes
    FROM column_catalog col
    LEFT JOIN primary_key_columns pk
        ON pk.object_schema = col.object_schema
       AND pk.object_name = col.object_name
       AND pk.column_name = col.column_name
    LEFT JOIN foreign_key_columns fk
        ON fk.object_schema = col.object_schema
       AND fk.object_name = col.object_name
       AND fk.column_name = col.column_name
    LEFT JOIN curated
        ON curated.object_schema = col.object_schema
       AND curated.object_name = col.object_name
       AND curated.column_name = col.column_name
), all_rows AS (
    SELECT * FROM schema_rows
    UNION ALL
    SELECT * FROM object_rows
    UNION ALL
    SELECT * FROM column_rows
)
INSERT INTO public.data_dictionary (
    object_schema,
    object_name,
    object_type,
    column_name,
    ordinal_position,
    data_type,
    is_nullable,
    column_default,
    is_primary_key,
    is_foreign_key,
    references_schema,
    references_table,
    references_column,
    semantic_group,
    lifecycle_role,
    description,
    notes
)
SELECT
    object_schema,
    object_name,
    object_type,
    column_name,
    ordinal_position,
    data_type,
    is_nullable,
    column_default,
    is_primary_key,
    is_foreign_key,
    references_schema,
    references_table,
    references_column,
    semantic_group,
    lifecycle_role,
    description,
    notes
FROM all_rows
ORDER BY
    object_schema,
    object_name,
    CASE object_type WHEN 'schema' THEN 0 WHEN 'table' THEN 1 WHEN 'view' THEN 1 WHEN 'column' THEN 2 ELSE 9 END,
    ordinal_position NULLS FIRST,
    column_name;

CREATE OR REPLACE VIEW rpt.data_dictionary AS
SELECT *
FROM public.data_dictionary
ORDER BY object_schema, object_name, object_type, ordinal_position NULLS FIRST, column_name;

COMMENT ON VIEW rpt.data_dictionary IS
    'Reporting view over public.data_dictionary for schema/table/view/column browsing.';

COMMIT;
