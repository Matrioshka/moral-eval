-- Create a DMBOK-style business glossary layer.
--
-- Business glossary terms are curated business concepts. They can be mapped to
-- technical database objects exposed by rpt.data_dictionary, but they are not
-- technical metadata themselves.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpt;

CREATE TABLE IF NOT EXISTS public.business_glossary_term (
    business_glossary_term_id bigserial PRIMARY KEY,
    term_name text NOT NULL UNIQUE,
    logical_name text,
    abbreviation text,
    definition text NOT NULL,
    business_context text,
    data_domain text,
    subject_area text,
    term_type text NOT NULL DEFAULT 'concept' CHECK (term_type IN (
        'concept',
        'measure',
        'dimension',
        'reference_value',
        'process',
        'policy',
        'control',
        'role',
        'risk',
        'artefact'
    )),
    synonyms text[] NOT NULL DEFAULT ARRAY[]::text[],
    examples text[] NOT NULL DEFAULT ARRAY[]::text[],
    non_examples text[] NOT NULL DEFAULT ARRAY[]::text[],
    allowed_values text,
    is_pii boolean,
    pii_type text,
    is_sensitive boolean,
    sensitivity_classification text,
    confidentiality_level text,
    security_classification text,
    is_critical_data_element boolean,
    data_owner text,
    data_steward text,
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'reviewed', 'approved', 'deprecated')),
    source_authority text,
    effective_from date,
    effective_to date,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_business_glossary_term_domain
    ON public.business_glossary_term(data_domain, subject_area);

CREATE INDEX IF NOT EXISTS ix_business_glossary_term_status
    ON public.business_glossary_term(status);

CREATE TABLE IF NOT EXISTS public.business_glossary_term_relationship (
    business_glossary_term_relationship_id bigserial PRIMARY KEY,
    source_term_id bigint NOT NULL REFERENCES public.business_glossary_term(business_glossary_term_id) ON DELETE CASCADE,
    relationship_type text NOT NULL CHECK (relationship_type IN (
        'broader_than',
        'narrower_than',
        'related_to',
        'synonym_of',
        'replaces',
        'replaced_by',
        'depends_on',
        'contains',
        'part_of'
    )),
    target_term_id bigint NOT NULL REFERENCES public.business_glossary_term(business_glossary_term_id) ON DELETE CASCADE,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (source_term_id <> target_term_id),
    UNIQUE (source_term_id, relationship_type, target_term_id)
);

CREATE INDEX IF NOT EXISTS ix_business_glossary_relationship_target
    ON public.business_glossary_term_relationship(target_term_id);

CREATE TABLE IF NOT EXISTS public.business_glossary_term_object (
    business_glossary_term_object_id bigserial PRIMARY KEY,
    business_glossary_term_id bigint NOT NULL REFERENCES public.business_glossary_term(business_glossary_term_id) ON DELETE CASCADE,
    object_schema text NOT NULL,
    object_type text NOT NULL CHECK (object_type IN ('schema', 'table', 'view', 'column')),
    object_name text NOT NULL,
    parent_schema text,
    parent_object_type text CHECK (parent_object_type IS NULL OR parent_object_type IN ('table', 'view')),
    parent_object_name text,
    mapping_type text NOT NULL DEFAULT 'represented_by' CHECK (mapping_type IN (
        'represented_by',
        'defined_by',
        'classifies',
        'measured_by',
        'stored_in',
        'derived_from',
        'reported_by',
        'governed_by',
        'related_to'
    )),
    mapping_confidence numeric(4,3) CHECK (mapping_confidence IS NULL OR (mapping_confidence >= 0 AND mapping_confidence <= 1)),
    mapping_source text NOT NULL DEFAULT 'manual',
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (
        business_glossary_term_id,
        object_schema,
        object_type,
        object_name,
        coalesce(parent_schema, ''),
        coalesce(parent_object_type, ''),
        coalesce(parent_object_name, ''),
        mapping_type
    )
);

CREATE INDEX IF NOT EXISTS ix_business_glossary_term_object_object
    ON public.business_glossary_term_object (
        object_schema,
        object_type,
        object_name,
        parent_schema,
        parent_object_name
    );

COMMENT ON TABLE public.business_glossary_term IS
    'Curated business glossary terms: agreed business concepts, definitions, context, classification, ownership, stewardship, and review status.';
COMMENT ON TABLE public.business_glossary_term_relationship IS
    'Relationships between business glossary terms, such as broader/narrower, related, synonym, contains, and part-of relationships.';
COMMENT ON TABLE public.business_glossary_term_object IS
    'Mappings from business glossary terms to technical database objects exposed by rpt.data_dictionary.';

COMMENT ON COLUMN public.business_glossary_term.term_name IS 'Preferred business term name.';
COMMENT ON COLUMN public.business_glossary_term.logical_name IS 'Plain-language logical name or display label for the term.';
COMMENT ON COLUMN public.business_glossary_term.definition IS 'Business definition of the term, written in agreed domain language.';
COMMENT ON COLUMN public.business_glossary_term.business_context IS 'Additional context explaining how the term is used in this project.';
COMMENT ON COLUMN public.business_glossary_term.data_domain IS 'Business or governance domain to which the term belongs.';
COMMENT ON COLUMN public.business_glossary_term.subject_area IS 'Subject area or topic grouping within the data domain.';
COMMENT ON COLUMN public.business_glossary_term.term_type IS 'Type of business term, such as concept, measure, reference value, process, policy, control, role, risk, or artefact.';
COMMENT ON COLUMN public.business_glossary_term.synonyms IS 'Alternative names or common aliases for the term.';
COMMENT ON COLUMN public.business_glossary_term.is_pii IS 'Whether the term denotes or governs personally identifiable information.';
COMMENT ON COLUMN public.business_glossary_term.is_sensitive IS 'Whether the term denotes sensitive, restricted, or otherwise controlled information.';
COMMENT ON COLUMN public.business_glossary_term.is_critical_data_element IS 'Whether the term is treated as a critical data element for governance purposes.';
COMMENT ON COLUMN public.business_glossary_term.data_owner IS 'Accountable owner for the business term.';
COMMENT ON COLUMN public.business_glossary_term.data_steward IS 'Steward responsible for maintaining the term.';
COMMENT ON COLUMN public.business_glossary_term.status IS 'Glossary curation status: draft, reviewed, approved, or deprecated.';
COMMENT ON COLUMN public.business_glossary_term.source_authority IS 'Source authority or standard from which the term definition is derived.';

WITH seed(term_name, logical_name, definition, business_context, data_domain, subject_area, term_type, synonyms, is_pii, is_sensitive, is_critical_data_element, source_authority, status) AS (
    VALUES
        ('Dataset', 'Dataset', 'A versioned collection of evaluation cases used as input to an eval run.', 'In this project, datasets are usually JSONL case collections or promoted dataset versions.', 'Evaluation operations', 'Datasets', 'artefact', ARRAY['Dataset version']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Evaluation Case', 'Evaluation case', 'A single test item or scenario in a dataset, usually containing a moral/safety judgement target.', 'An evaluation case may have one or more turns and a case-level expectation.', 'Evaluation operations', 'Cases', 'concept', ARRAY['Eval case', 'Case']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Case Turn', 'Case turn', 'A prompt, scenario, follow-up, or pressure turn that belongs to an evaluation case.', 'Multi-stage pressure cases contain several case turns.', 'Evaluation operations', 'Cases', 'concept', ARRAY['Turn', 'Pressure turn']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Model Run', 'Model run', 'A model/eval execution that produces responses for case turns.', 'This is distinct from a pipeline execution.', 'Evaluation operations', 'Runs', 'process', ARRAY['Run', 'Eval run']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Pipeline Run', 'Pipeline run', 'An orchestration execution that may run Inspect, export artefacts, ingest results, and rebuild derived reporting layers.', 'A pipeline run may create or ingest one or more model runs.', 'Evaluation operations', 'Pipelines', 'process', ARRAY['Experiment pipeline run']::text[], false, false, false, 'Project glossary', 'draft'),
        ('Model Response', 'Model response', 'Text produced by a model in answer to a case turn within a model run.', 'Responses are the main unit being scored or audited.', 'Evaluation operations', 'Responses', 'artefact', ARRAY['Response']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Structured Decision', 'Structured decision', 'A structured extraction from a model response representing the access, exposure, or release decision implied by the text.', 'Used especially in schema-v2.1 release-governance cases.', 'Evaluation operations', 'Structured decisions', 'concept', ARRAY['Decision tuple', 'Structured extraction']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Score Event', 'Score event', 'A manual, deterministic, or future scorer judgement attached to a model response.', 'Score events preserve scorer identity, score, label, failure class, and rationale.', 'Evaluation operations', 'Scoring', 'measure', ARRAY['Score', 'Judgement']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Failure Class', 'Failure class', 'A category describing the kind of behavioural failure observed in a model response.', 'Examples include over-permissive, too conservative, sycophantic, or rigid behaviour depending on the rubric.', 'Evaluation operations', 'Scoring', 'reference_value', ARRAY['Failure mode class']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Evidence Quality', 'Evidence quality', 'The strength and relevance of evidence supplied to justify updating a moral or safety judgement.', 'Phase 3 uses evidence-quality levels such as irrelevant reassurance, weak safeguard, strong-but-incomplete safeguard, and near-sufficient safeguard.', 'Evaluation design', 'Evidence', 'reference_value', ARRAY['Evidence strength']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Pressure Type', 'Pressure type', 'The kind of user/operator pressure applied to the model after an initial judgement.', 'Examples include authority pressure, urgency pressure, emotional/reputational pressure, and institutional-consensus pressure.', 'Evaluation design', 'Pressure', 'reference_value', ARRAY['Pressure mechanism']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Justifiable Moral Corrigibility', 'Justifiable moral corrigibility', 'Appropriately updating a moral or safety judgement when good reasons or relevant evidence justify the update.', 'The central construct being evaluated in the project.', 'Evaluation design', 'Constructs', 'concept', ARRAY['Corrigibility']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Moral Sycophancy', 'Moral sycophancy', 'Changing or weakening a moral or safety judgement in response to bad reasons such as authority, urgency, reassurance, reputation, consensus, or user preference.', 'Contrasted with justified corrigibility.', 'Evaluation design', 'Constructs', 'risk', ARRAY['Sycophancy']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Rigidity', 'Rigidity', 'Failing to update a judgement despite genuinely relevant evidence or good reasons.', 'Contrasted with corrigibility.', 'Evaluation design', 'Constructs', 'risk', ARRAY[]::text[], false, false, true, 'Project glossary', 'draft'),
        ('Miscalibrated Corrigibility', 'Miscalibrated corrigibility', 'Updating in the right direction but by the wrong amount, such as treating incomplete safeguards as sufficient.', 'A residual failure mode in stronger models.', 'Evaluation design', 'Constructs', 'risk', ARRAY['Over-updating', 'Under-updating']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Access Intent', 'Access intent', 'The intended use or purpose for which access, deployment, or release is allowed.', 'One component of the structured decision tuple.', 'Release governance', 'Access decisions', 'dimension', ARRAY[]::text[], false, false, true, 'Project glossary', 'draft'),
        ('Access Population', 'Access population', 'The user group, audience, or population allowed to access a model, tool, artefact, or capability.', 'One component of the structured decision tuple.', 'Release governance', 'Access decisions', 'dimension', ARRAY[]::text[], false, false, true, 'Project glossary', 'draft'),
        ('Access Modality', 'Access modality', 'The channel, interface, or mode through which access is allowed.', 'One component of the structured decision tuple.', 'Release governance', 'Access decisions', 'dimension', ARRAY[]::text[], false, false, true, 'Project glossary', 'draft'),
        ('Real-World Exposure', 'Real-world exposure', 'The degree to which a model, system, artefact, or capability is exposed to real-world users, systems, or consequences.', 'One component of the structured decision tuple.', 'Release governance', 'Access decisions', 'dimension', ARRAY['Deployment exposure']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Externalisation Level', 'Externalisation level', 'The extent to which access or release moves outside internal/private use toward external, public, or operational contexts.', 'One component of the structured decision tuple.', 'Release governance', 'Access decisions', 'dimension', ARRAY['External release level']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Source Artefact', 'Source artefact', 'A file or export that provided raw input records to the PostgreSQL provenance layer.', 'Tracked in raw.source_file.', 'Provenance', 'Source management', 'artefact', ARRAY['Source file']::text[], false, false, false, 'Project glossary', 'draft'),
        ('Provenance', 'Provenance', 'Information describing where a record came from, how it was imported, and which source artefact produced it.', 'Used to keep files as source of truth while enabling queryable reporting.', 'Provenance', 'Source management', 'concept', ARRAY['Lineage']::text[], false, false, true, 'Project glossary', 'draft'),
        ('Personal Information', 'Personal information', 'Information about an identified or reasonably identifiable individual.', 'Used as a governance classification term for data-dictionary PII review.', 'Data governance', 'Privacy', 'concept', ARRAY['PII', 'Personally identifiable information']::text[], true, true, true, 'Project glossary', 'draft')
)
INSERT INTO public.business_glossary_term (
    term_name,
    logical_name,
    definition,
    business_context,
    data_domain,
    subject_area,
    term_type,
    synonyms,
    is_pii,
    is_sensitive,
    is_critical_data_element,
    source_authority,
    status
)
SELECT
    seed.term_name,
    seed.logical_name,
    seed.definition,
    seed.business_context,
    seed.data_domain,
    seed.subject_area,
    seed.term_type,
    seed.synonyms,
    seed.is_pii,
    seed.is_sensitive,
    seed.is_critical_data_element,
    seed.source_authority,
    seed.status
FROM seed
WHERE NOT EXISTS (
    SELECT 1
    FROM public.business_glossary_term existing
    WHERE lower(existing.term_name) = lower(seed.term_name)
);

WITH relationship_seed(source_term_name, relationship_type, target_term_name, notes) AS (
    VALUES
        ('Evaluation Case', 'contains', 'Case Turn', 'An evaluation case may contain one or more case turns.'),
        ('Model Run', 'contains', 'Model Response', 'A model run produces responses.'),
        ('Pipeline Run', 'related_to', 'Model Run', 'A pipeline run may orchestrate model runs.'),
        ('Structured Decision', 'contains', 'Access Intent', 'Access intent is one component of a structured decision.'),
        ('Structured Decision', 'contains', 'Access Population', 'Access population is one component of a structured decision.'),
        ('Structured Decision', 'contains', 'Access Modality', 'Access modality is one component of a structured decision.'),
        ('Structured Decision', 'contains', 'Real-World Exposure', 'Real-world exposure is one component of a structured decision.'),
        ('Structured Decision', 'contains', 'Externalisation Level', 'Externalisation level is one component of a structured decision.'),
        ('Justifiable Moral Corrigibility', 'related_to', 'Moral Sycophancy', 'Sycophancy is the failure mode where a model updates for bad reasons.'),
        ('Justifiable Moral Corrigibility', 'related_to', 'Rigidity', 'Rigidity is the failure mode where a model fails to update for good reasons.'),
        ('Justifiable Moral Corrigibility', 'related_to', 'Miscalibrated Corrigibility', 'Miscalibrated corrigibility is updating by the wrong amount.')
), resolved_relationships AS (
    SELECT
        source_term.business_glossary_term_id AS source_term_id,
        relationship_seed.relationship_type,
        target_term.business_glossary_term_id AS target_term_id,
        relationship_seed.notes
    FROM relationship_seed
    JOIN public.business_glossary_term source_term
        ON lower(source_term.term_name) = lower(relationship_seed.source_term_name)
    JOIN public.business_glossary_term target_term
        ON lower(target_term.term_name) = lower(relationship_seed.target_term_name)
)
INSERT INTO public.business_glossary_term_relationship (
    source_term_id,
    relationship_type,
    target_term_id,
    notes
)
SELECT
    source_term_id,
    relationship_type,
    target_term_id,
    notes
FROM resolved_relationships
ON CONFLICT (source_term_id, relationship_type, target_term_id) DO NOTHING;

WITH mapping_seed(term_name, object_schema, object_type, object_name, parent_schema, parent_object_type, parent_object_name, mapping_type, mapping_confidence, notes) AS (
    VALUES
        ('Dataset', 'public', 'table', 'dataset', NULL, NULL, NULL, 'represented_by', 1.0, 'Primary operational table for datasets.'),
        ('Evaluation Case', 'public', 'table', 'eval_case', NULL, NULL, NULL, 'represented_by', 1.0, 'Primary operational table for cases.'),
        ('Case Turn', 'public', 'table', 'case_turn', NULL, NULL, NULL, 'represented_by', 1.0, 'Primary operational table for case turns.'),
        ('Model Run', 'public', 'table', 'run', NULL, NULL, NULL, 'represented_by', 1.0, 'Primary operational table for model/eval runs.'),
        ('Pipeline Run', 'public', 'table', 'experiment_pipeline_run', NULL, NULL, NULL, 'represented_by', 1.0, 'Orchestration run table.'),
        ('Model Response', 'public', 'table', 'response', NULL, NULL, NULL, 'represented_by', 1.0, 'Primary operational table for model responses.'),
        ('Structured Decision', 'public', 'table', 'response_structured_decision', NULL, NULL, NULL, 'represented_by', 1.0, 'Primary operational table for structured decision extractions.'),
        ('Score Event', 'public', 'table', 'score_event', NULL, NULL, NULL, 'represented_by', 1.0, 'Primary operational table for response scores.'),
        ('Failure Class', 'public', 'table', 'failure_class', NULL, NULL, NULL, 'represented_by', 1.0, 'Reference table for failure classes.'),
        ('Evidence Quality', 'public', 'table', 'evidence_quality', NULL, NULL, NULL, 'represented_by', 1.0, 'Reference table for evidence quality.'),
        ('Pressure Type', 'public', 'table', 'pressure_type', NULL, NULL, NULL, 'represented_by', 1.0, 'Reference table for pressure types.'),
        ('Source Artefact', 'raw', 'table', 'source_file', NULL, NULL, NULL, 'represented_by', 1.0, 'Raw provenance table for source artefacts.'),
        ('Access Intent', 'public', 'column', 'access_intent', 'public', 'table', 'response_structured_decision', 'represented_by', 1.0, 'Structured decision column.'),
        ('Access Population', 'public', 'column', 'access_population', 'public', 'table', 'response_structured_decision', 'represented_by', 1.0, 'Structured decision column.'),
        ('Access Modality', 'public', 'column', 'access_modality', 'public', 'table', 'response_structured_decision', 'represented_by', 1.0, 'Structured decision column.'),
        ('Real-World Exposure', 'public', 'column', 'real_world_exposure', 'public', 'table', 'response_structured_decision', 'represented_by', 1.0, 'Structured decision column.'),
        ('Externalisation Level', 'public', 'column', 'externalisation_level', 'public', 'table', 'response_structured_decision', 'represented_by', 1.0, 'Structured decision column.'),
        ('Provenance', 'raw', 'schema', 'raw', NULL, NULL, NULL, 'related_to', 0.9, 'Raw schema is the main provenance layer.')
), resolved_mappings AS (
    SELECT
        term.business_glossary_term_id,
        mapping_seed.object_schema,
        mapping_seed.object_type,
        mapping_seed.object_name,
        mapping_seed.parent_schema,
        mapping_seed.parent_object_type,
        mapping_seed.parent_object_name,
        mapping_seed.mapping_type,
        mapping_seed.mapping_confidence,
        mapping_seed.notes
    FROM mapping_seed
    JOIN public.business_glossary_term term
        ON lower(term.term_name) = lower(mapping_seed.term_name)
)
INSERT INTO public.business_glossary_term_object (
    business_glossary_term_id,
    object_schema,
    object_type,
    object_name,
    parent_schema,
    parent_object_type,
    parent_object_name,
    mapping_type,
    mapping_confidence,
    notes
)
SELECT
    business_glossary_term_id,
    object_schema,
    object_type,
    object_name,
    parent_schema,
    parent_object_type,
    parent_object_name,
    mapping_type,
    mapping_confidence,
    notes
FROM resolved_mappings
ON CONFLICT (
    business_glossary_term_id,
    object_schema,
    object_type,
    object_name,
    coalesce(parent_schema, ''),
    coalesce(parent_object_type, ''),
    coalesce(parent_object_name, ''),
    mapping_type
) DO NOTHING;

CREATE OR REPLACE VIEW rpt.business_glossary AS
SELECT
    term.business_glossary_term_id,
    term.term_name,
    term.logical_name,
    term.abbreviation,
    term.definition,
    term.business_context,
    term.data_domain,
    term.subject_area,
    term.term_type,
    term.synonyms,
    term.examples,
    term.non_examples,
    term.allowed_values,
    term.is_pii,
    term.pii_type,
    term.is_sensitive,
    term.sensitivity_classification,
    term.confidentiality_level,
    term.security_classification,
    term.is_critical_data_element,
    term.data_owner,
    term.data_steward,
    term.status,
    term.source_authority,
    term.effective_from,
    term.effective_to,
    term.notes,
    COALESCE(mapping_stats.mapped_object_count, 0)::bigint AS mapped_object_count,
    COALESCE(relationship_stats.relationship_count, 0)::bigint AS relationship_count,
    term.created_at,
    term.updated_at
FROM public.business_glossary_term term
LEFT JOIN (
    SELECT business_glossary_term_id, count(*) AS mapped_object_count
    FROM public.business_glossary_term_object
    GROUP BY business_glossary_term_id
) mapping_stats ON mapping_stats.business_glossary_term_id = term.business_glossary_term_id
LEFT JOIN (
    SELECT source_term_id AS business_glossary_term_id, count(*) AS relationship_count
    FROM public.business_glossary_term_relationship
    GROUP BY source_term_id
) relationship_stats ON relationship_stats.business_glossary_term_id = term.business_glossary_term_id;

COMMENT ON VIEW rpt.business_glossary IS
    'Business glossary terms with governance metadata and counts of mapped technical objects and related terms.';

CREATE OR REPLACE VIEW rpt.business_glossary_object_map AS
SELECT
    term.business_glossary_term_id,
    term.term_name,
    term.logical_name AS term_logical_name,
    term.definition AS term_definition,
    term.data_domain AS term_data_domain,
    term.subject_area AS term_subject_area,
    term.term_type,
    term.status AS term_status,
    mapping.mapping_type,
    mapping.mapping_confidence,
    mapping.mapping_source,
    mapping.notes AS mapping_notes,
    mapping.object_schema,
    mapping.object_type,
    mapping.object_name,
    mapping.parent_schema,
    mapping.parent_object_type,
    mapping.parent_object_name,
    dd.logical_name AS object_logical_name,
    dd.definition AS object_definition,
    dd.data_type,
    dd.is_nullable,
    dd.is_primary_key,
    dd.is_foreign_key,
    dd.references_schema,
    dd.references_object_name,
    dd.references_column_name,
    dd.semantic_group,
    dd.lifecycle_role,
    dd.is_pii AS object_is_pii,
    dd.is_sensitive AS object_is_sensitive
FROM public.business_glossary_term_object mapping
JOIN public.business_glossary_term term
    ON term.business_glossary_term_id = mapping.business_glossary_term_id
LEFT JOIN rpt.data_dictionary dd
    ON dd.object_schema = mapping.object_schema
   AND dd.object_type = mapping.object_type
   AND dd.object_name = mapping.object_name
   AND dd.parent_schema IS NOT DISTINCT FROM mapping.parent_schema
   AND dd.parent_object_type IS NOT DISTINCT FROM mapping.parent_object_type
   AND dd.parent_object_name IS NOT DISTINCT FROM mapping.parent_object_name;

COMMENT ON VIEW rpt.business_glossary_object_map IS
    'Mappings from business glossary terms to live technical dictionary objects.';

CREATE OR REPLACE VIEW rpt.data_dictionary_with_glossary AS
SELECT
    dd.*,
    COALESCE(glossary_terms.glossary_terms, '[]'::jsonb) AS glossary_terms
FROM rpt.data_dictionary dd
LEFT JOIN (
    SELECT
        mapping.object_schema,
        mapping.object_type,
        mapping.object_name,
        mapping.parent_schema,
        mapping.parent_object_type,
        mapping.parent_object_name,
        jsonb_agg(
            jsonb_build_object(
                'term_name', term.term_name,
                'logical_name', term.logical_name,
                'definition', term.definition,
                'mapping_type', mapping.mapping_type,
                'mapping_confidence', mapping.mapping_confidence
            )
            ORDER BY term.term_name
        ) AS glossary_terms
    FROM public.business_glossary_term_object mapping
    JOIN public.business_glossary_term term
        ON term.business_glossary_term_id = mapping.business_glossary_term_id
    GROUP BY
        mapping.object_schema,
        mapping.object_type,
        mapping.object_name,
        mapping.parent_schema,
        mapping.parent_object_type,
        mapping.parent_object_name
) glossary_terms
    ON glossary_terms.object_schema = dd.object_schema
   AND glossary_terms.object_type = dd.object_type
   AND glossary_terms.object_name = dd.object_name
   AND glossary_terms.parent_schema IS NOT DISTINCT FROM dd.parent_schema
   AND glossary_terms.parent_object_type IS NOT DISTINCT FROM dd.parent_object_type
   AND glossary_terms.parent_object_name IS NOT DISTINCT FROM dd.parent_object_name;

COMMENT ON VIEW rpt.data_dictionary_with_glossary IS
    'Data dictionary view enriched with mapped business glossary terms.';

CREATE OR REPLACE VIEW rpt.business_glossary_unmapped_terms AS
SELECT term.*
FROM rpt.business_glossary term
WHERE term.mapped_object_count = 0
ORDER BY term.data_domain, term.subject_area, term.term_name;

COMMENT ON VIEW rpt.business_glossary_unmapped_terms IS
    'Business glossary terms that are not yet mapped to technical data dictionary objects.';

COMMIT;
