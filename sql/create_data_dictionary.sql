-- Create the data-dictionary governance table and live catalog-backed views.
--
-- PostgreSQL comments are the canonical object definitions.
-- public.data_dictionary_entry stores only curated governance/business metadata.
-- rpt.technical_data_dictionary pulls technical metadata live from PostgreSQL.
-- rpt.data_dictionary joins live technical metadata, native comments, and curated metadata.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpt;

DROP VIEW IF EXISTS rpt.data_dictionary_governance_gap;
DROP VIEW IF EXISTS rpt.data_dictionary_missing_comment;
DROP VIEW IF EXISTS rpt.data_dictionary;
DROP VIEW IF EXISTS rpt.technical_data_dictionary;
DROP VIEW IF EXISTS rpt.postgres_object_comments;
DROP TABLE IF EXISTS public.data_dictionary;

CREATE TABLE IF NOT EXISTS public.data_dictionary_entry (
    data_dictionary_entry_id bigserial PRIMARY KEY,
    object_schema text NOT NULL,
    object_type text NOT NULL CHECK (object_type IN ('schema', 'table', 'view', 'column')),
    object_name text NOT NULL,
    parent_schema text,
    parent_object_type text CHECK (parent_object_type IS NULL OR parent_object_type IN ('table', 'view')),
    parent_object_name text,
    logical_name text,
    business_name text,
    data_domain text,
    subject_area text,
    semantic_group text,
    lifecycle_role text,
    is_pii boolean,
    pii_type text,
    is_sensitive boolean,
    sensitivity_classification text,
    confidentiality_level text,
    security_classification text,
    is_critical_data_element boolean,
    data_owner text,
    data_steward text,
    review_status text NOT NULL DEFAULT 'draft',
    reviewed_at timestamptz,
    reviewed_by text,
    allowed_values text,
    example_value text,
    validation_rule text,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_data_dictionary_entry_object
    ON public.data_dictionary_entry (
        object_schema,
        object_type,
        object_name,
        coalesce(parent_schema, ''),
        coalesce(parent_object_type, ''),
        coalesce(parent_object_name, '')
    );

CREATE INDEX IF NOT EXISTS ix_data_dictionary_entry_parent
    ON public.data_dictionary_entry(parent_schema, parent_object_name, object_type);

COMMENT ON TABLE public.data_dictionary_entry IS
    'Curated DMBOK-style governance and business metadata for database objects. Technical metadata is pulled live from PostgreSQL catalog views.';

WITH seed(
    object_schema,
    object_type,
    object_name,
    parent_schema,
    parent_object_type,
    parent_object_name,
    logical_name,
    semantic_group,
    lifecycle_role,
    data_domain,
    subject_area,
    review_status
) AS (
    VALUES
        ('raw', 'schema', 'raw', NULL, NULL, NULL, 'Raw provenance schema', 'provenance', 'source_landing', 'Evaluation provenance', 'PostgreSQL metadata', 'draft'),
        ('public', 'schema', 'public', NULL, NULL, NULL, 'Operational schema', 'operational', 'application_operational', 'Evaluation operations', 'PostgreSQL metadata', 'draft'),
        ('rpt', 'schema', 'rpt', NULL, NULL, NULL, 'Reporting schema', 'reporting', 'reporting_surface', 'Evaluation reporting', 'PostgreSQL metadata', 'draft'),
        ('public', 'table', 'dataset', NULL, NULL, NULL, 'Dataset', 'operational', 'application_operational', 'Evaluation operations', 'Datasets', 'draft'),
        ('public', 'table', 'run', NULL, NULL, NULL, 'Model run', 'operational', 'application_operational', 'Evaluation operations', 'Runs', 'draft'),
        ('public', 'table', 'eval_case', NULL, NULL, NULL, 'Evaluation case', 'operational', 'application_operational', 'Evaluation operations', 'Cases', 'draft'),
        ('public', 'table', 'case_turn', NULL, NULL, NULL, 'Case turn', 'operational', 'application_operational', 'Evaluation operations', 'Cases', 'draft'),
        ('public', 'table', 'case_expectation', NULL, NULL, NULL, 'Case expectation', 'operational', 'application_operational', 'Evaluation operations', 'Case expectations', 'draft'),
        ('public', 'table', 'response', NULL, NULL, NULL, 'Model response', 'operational', 'application_operational', 'Evaluation operations', 'Responses', 'draft'),
        ('public', 'table', 'response_structured_decision', NULL, NULL, NULL, 'Response structured decision', 'operational', 'application_operational', 'Evaluation operations', 'Structured decisions', 'draft'),
        ('public', 'table', 'score_event', NULL, NULL, NULL, 'Score event', 'operational', 'application_operational', 'Evaluation operations', 'Scoring', 'draft'),
        ('public', 'table', 'data_dictionary_entry', NULL, NULL, NULL, 'Data dictionary entry', 'metadata', 'governance_metadata', 'Metadata management', 'Data dictionary', 'draft'),
        ('rpt', 'view', 'run_summary', NULL, NULL, NULL, 'Run summary', 'reporting', 'reporting_surface', 'Evaluation reporting', 'Run pages', 'draft'),
        ('rpt', 'view', 'run_detail', NULL, NULL, NULL, 'Run detail', 'reporting', 'reporting_surface', 'Evaluation reporting', 'Run pages', 'draft'),
        ('rpt', 'view', 'case_run_trace', NULL, NULL, NULL, 'Case run trace', 'reporting', 'reporting_surface', 'Evaluation reporting', 'Case cards', 'draft'),
        ('rpt', 'view', 'technical_data_dictionary', NULL, NULL, NULL, 'Technical data dictionary', 'metadata', 'reporting_surface', 'Metadata management', 'Data dictionary', 'draft'),
        ('rpt', 'view', 'data_dictionary', NULL, NULL, NULL, 'Data dictionary', 'metadata', 'reporting_surface', 'Metadata management', 'Data dictionary', 'draft')
)
INSERT INTO public.data_dictionary_entry (
    object_schema,
    object_type,
    object_name,
    parent_schema,
    parent_object_type,
    parent_object_name,
    logical_name,
    semantic_group,
    lifecycle_role,
    data_domain,
    subject_area,
    review_status
)
SELECT
    seed.object_schema,
    seed.object_type,
    seed.object_name,
    seed.parent_schema,
    seed.parent_object_type,
    seed.parent_object_name,
    seed.logical_name,
    seed.semantic_group,
    seed.lifecycle_role,
    seed.data_domain,
    seed.subject_area,
    seed.review_status
FROM seed
WHERE NOT EXISTS (
    SELECT 1
    FROM public.data_dictionary_entry existing
    WHERE existing.object_schema = seed.object_schema
      AND existing.object_type = seed.object_type
      AND existing.object_name = seed.object_name
      AND existing.parent_schema IS NOT DISTINCT FROM seed.parent_schema
      AND existing.parent_object_type IS NOT DISTINCT FROM seed.parent_object_type
      AND existing.parent_object_name IS NOT DISTINCT FROM seed.parent_object_name
);

CREATE VIEW rpt.technical_data_dictionary AS
WITH schema_rows AS (
    SELECT
        ns.nspname AS object_schema,
        'schema'::text AS object_type,
        ns.nspname AS object_name,
        NULL::text AS parent_schema,
        NULL::text AS parent_object_type,
        NULL::text AS parent_object_name,
        NULL::integer AS ordinal_position,
        NULL::text AS data_type,
        NULL::boolean AS is_nullable,
        NULL::text AS column_default,
        false AS is_primary_key,
        false AS is_foreign_key,
        NULL::text AS references_schema,
        NULL::text AS references_object_name,
        NULL::text AS references_column_name,
        obj_description(ns.oid, 'pg_namespace') AS postgres_comment,
        'Schema ' || ns.nspname || '.' AS generated_definition,
        CASE ns.nspname
            WHEN 'raw' THEN 'provenance'
            WHEN 'public' THEN 'operational'
            WHEN 'rpt' THEN 'reporting'
            ELSE 'unspecified'
        END AS inferred_semantic_group,
        CASE ns.nspname
            WHEN 'raw' THEN 'source_landing'
            WHEN 'public' THEN 'application_operational'
            WHEN 'rpt' THEN 'reporting_surface'
            ELSE 'unspecified'
        END AS inferred_lifecycle_role
    FROM pg_namespace ns
    WHERE ns.nspname IN ('raw', 'public', 'rpt')
), object_rows AS (
    SELECT
        ns.nspname AS object_schema,
        CASE cls.relkind
            WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'view'
            ELSE 'table'
        END AS object_type,
        cls.relname AS object_name,
        NULL::text AS parent_schema,
        NULL::text AS parent_object_type,
        NULL::text AS parent_object_name,
        NULL::integer AS ordinal_position,
        NULL::text AS data_type,
        NULL::boolean AS is_nullable,
        NULL::text AS column_default,
        false AS is_primary_key,
        false AS is_foreign_key,
        NULL::text AS references_schema,
        NULL::text AS references_object_name,
        NULL::text AS references_column_name,
        obj_description(cls.oid, 'pg_class') AS postgres_comment,
        initcap(replace(cls.relname, '_', ' ')) || ' ' || CASE cls.relkind WHEN 'v' THEN 'view' WHEN 'm' THEN 'view' ELSE 'table' END || '.' AS generated_definition,
        CASE
            WHEN ns.nspname = 'raw' THEN 'provenance'
            WHEN ns.nspname = 'rpt' THEN 'reporting'
            WHEN cls.relname IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'reference'
            WHEN cls.relname IN ('data_dictionary_entry') THEN 'metadata'
            ELSE 'operational'
        END AS inferred_semantic_group,
        CASE
            WHEN ns.nspname = 'raw' THEN 'source_landing'
            WHEN ns.nspname = 'rpt' THEN 'reporting_surface'
            WHEN cls.relname IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'lookup'
            WHEN cls.relname IN ('data_dictionary_entry') THEN 'governance_metadata'
            ELSE 'application_operational'
        END AS inferred_lifecycle_role
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname IN ('raw', 'public', 'rpt')
      AND cls.relkind IN ('r', 'p', 'v', 'm')
), column_catalog AS (
    SELECT
        ns.nspname AS object_schema,
        'column'::text AS object_type,
        att.attname AS object_name,
        ns.nspname AS parent_schema,
        CASE cls.relkind
            WHEN 'v' THEN 'view'
            WHEN 'm' THEN 'view'
            ELSE 'table'
        END AS parent_object_type,
        cls.relname AS parent_object_name,
        att.attnum AS ordinal_position,
        format_type(att.atttypid, att.atttypmod) AS data_type,
        NOT att.attnotnull AS is_nullable,
        pg_get_expr(def.adbin, def.adrelid) AS column_default,
        col_description(cls.oid, att.attnum) AS postgres_comment,
        initcap(replace(att.attname, '_', ' ')) || ' column on ' || ns.nspname || '.' || cls.relname || '.' AS generated_definition,
        CASE
            WHEN ns.nspname = 'raw' THEN 'provenance'
            WHEN ns.nspname = 'rpt' THEN 'reporting'
            WHEN cls.relname IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'reference'
            WHEN cls.relname IN ('data_dictionary_entry') THEN 'metadata'
            ELSE 'operational'
        END AS inferred_semantic_group,
        CASE
            WHEN ns.nspname = 'raw' THEN 'source_landing_column'
            WHEN ns.nspname = 'rpt' THEN 'reporting_column'
            WHEN cls.relname IN ('moral_domain', 'turn_type', 'pressure_type', 'evidence_quality', 'scorer', 'rubric', 'failure_class') THEN 'lookup_column'
            WHEN cls.relname IN ('data_dictionary_entry') THEN 'governance_metadata_column'
            ELSE 'application_operational_column'
        END AS inferred_lifecycle_role,
        cls.oid AS table_oid
    FROM pg_class cls
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum > 0 AND NOT att.attisdropped
    LEFT JOIN pg_attrdef def ON def.adrelid = cls.oid AND def.adnum = att.attnum
    WHERE ns.nspname IN ('raw', 'public', 'rpt')
      AND cls.relkind IN ('r', 'p', 'v', 'm')
), primary_key_columns AS (
    SELECT
        ns.nspname AS parent_schema,
        cls.relname AS parent_object_name,
        att.attname AS object_name
    FROM pg_constraint con
    JOIN pg_class cls ON cls.oid = con.conrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    JOIN unnest(con.conkey) AS key_attnum(attnum) ON true
    JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum = key_attnum.attnum
    WHERE con.contype = 'p'
), foreign_key_columns AS (
    SELECT
        ns.nspname AS parent_schema,
        cls.relname AS parent_object_name,
        att.attname AS object_name,
        ref_ns.nspname AS references_schema,
        ref_cls.relname AS references_object_name,
        ref_att.attname AS references_column_name
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
        col.object_type,
        col.object_name,
        col.parent_schema,
        col.parent_object_type,
        col.parent_object_name,
        col.ordinal_position,
        col.data_type,
        col.is_nullable,
        col.column_default,
        (pk.object_name IS NOT NULL) AS is_primary_key,
        (fk.object_name IS NOT NULL) AS is_foreign_key,
        fk.references_schema,
        fk.references_object_name,
        fk.references_column_name,
        col.postgres_comment,
        col.generated_definition,
        col.inferred_semantic_group,
        col.inferred_lifecycle_role
    FROM column_catalog col
    LEFT JOIN primary_key_columns pk
        ON pk.parent_schema = col.parent_schema
       AND pk.parent_object_name = col.parent_object_name
       AND pk.object_name = col.object_name
    LEFT JOIN foreign_key_columns fk
        ON fk.parent_schema = col.parent_schema
       AND fk.parent_object_name = col.parent_object_name
       AND fk.object_name = col.object_name
), all_rows AS (
    SELECT * FROM schema_rows
    UNION ALL
    SELECT * FROM object_rows
    UNION ALL
    SELECT * FROM column_rows
)
SELECT
    object_schema,
    object_type,
    object_name,
    parent_schema,
    parent_object_type,
    parent_object_name,
    ordinal_position,
    data_type,
    is_nullable,
    column_default,
    is_primary_key,
    is_foreign_key,
    references_schema,
    references_object_name,
    references_column_name,
    postgres_comment,
    generated_definition,
    inferred_semantic_group,
    inferred_lifecycle_role
FROM all_rows;

COMMENT ON VIEW rpt.technical_data_dictionary IS
    'Live technical metadata for raw, public, and rpt schemas, tables, views, and columns, sourced from PostgreSQL catalog tables and native comments.';

CREATE VIEW rpt.data_dictionary AS
SELECT
    tech.object_schema,
    tech.object_type,
    tech.object_name,
    tech.parent_schema,
    tech.parent_object_type,
    tech.parent_object_name,
    entry.logical_name,
    entry.business_name,
    tech.postgres_comment AS definition,
    tech.generated_definition,
    COALESCE(tech.postgres_comment, tech.generated_definition) AS description,
    tech.ordinal_position,
    tech.data_type,
    tech.is_nullable,
    tech.column_default,
    tech.is_primary_key,
    tech.is_foreign_key,
    tech.references_schema,
    tech.references_object_name,
    tech.references_column_name,
    COALESCE(entry.semantic_group, tech.inferred_semantic_group) AS semantic_group,
    COALESCE(entry.lifecycle_role, tech.inferred_lifecycle_role) AS lifecycle_role,
    entry.data_domain,
    entry.subject_area,
    entry.is_pii,
    entry.pii_type,
    entry.is_sensitive,
    entry.sensitivity_classification,
    entry.confidentiality_level,
    entry.security_classification,
    entry.is_critical_data_element,
    entry.data_owner,
    entry.data_steward,
    entry.review_status,
    entry.reviewed_at,
    entry.reviewed_by,
    entry.allowed_values,
    entry.example_value,
    entry.validation_rule,
    entry.notes,
    entry.data_dictionary_entry_id,
    (entry.data_dictionary_entry_id IS NOT NULL) AS has_governance_entry
FROM rpt.technical_data_dictionary tech
LEFT JOIN public.data_dictionary_entry entry
    ON entry.object_schema = tech.object_schema
   AND entry.object_type = tech.object_type
   AND entry.object_name = tech.object_name
   AND entry.parent_schema IS NOT DISTINCT FROM tech.parent_schema
   AND entry.parent_object_type IS NOT DISTINCT FROM tech.parent_object_type
   AND entry.parent_object_name IS NOT DISTINCT FROM tech.parent_object_name;

COMMENT ON VIEW rpt.data_dictionary IS
    'Joined data dictionary for web/interface use. Technical metadata and definitions come live from PostgreSQL; governance metadata comes from public.data_dictionary_entry.';

CREATE VIEW rpt.data_dictionary_missing_comment AS
SELECT *
FROM rpt.data_dictionary
WHERE definition IS NULL
ORDER BY object_schema, parent_object_name NULLS FIRST, object_type, ordinal_position NULLS FIRST, object_name;

COMMENT ON VIEW rpt.data_dictionary_missing_comment IS
    'Database objects in raw, public, or rpt without native PostgreSQL COMMENT definitions.';

CREATE VIEW rpt.data_dictionary_governance_gap AS
SELECT
    dd.*,
    array_remove(ARRAY[
        CASE WHEN dd.logical_name IS NULL THEN 'missing_logical_name' END,
        CASE WHEN dd.object_type = 'column' AND dd.is_pii IS NULL THEN 'missing_pii_review' END,
        CASE WHEN dd.review_status IS NULL THEN 'missing_review_status' END,
        CASE WHEN dd.data_domain IS NULL THEN 'missing_data_domain' END
    ], NULL) AS gap_reasons
FROM rpt.data_dictionary dd
WHERE dd.logical_name IS NULL
   OR (dd.object_type = 'column' AND dd.is_pii IS NULL)
   OR dd.review_status IS NULL
   OR dd.data_domain IS NULL
ORDER BY dd.object_schema, dd.parent_object_name NULLS FIRST, dd.object_type, dd.ordinal_position NULLS FIRST, dd.object_name;

COMMENT ON VIEW rpt.data_dictionary_governance_gap IS
    'Objects missing curated governance metadata such as logical name, PII review, review status, or data domain.';

COMMIT;
