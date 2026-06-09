-- Add hierarchical glossary support and seed miscalibrated-corrigibility subtypes.
--
-- The relationship table remains the general graph of glossary relationships.
-- primary_parent_term_id and display_order support a preferred browsing tree for
-- web/UI use.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpt;

ALTER TABLE public.business_glossary_term
    ADD COLUMN IF NOT EXISTS primary_parent_term_id bigint,
    ADD COLUMN IF NOT EXISTS display_order integer,
    ADD COLUMN IF NOT EXISTS taxonomy_scope text,
    ADD COLUMN IF NOT EXISTS is_taxonomy_node boolean NOT NULL DEFAULT false;

DO $$
BEGIN
    ALTER TABLE public.business_glossary_term
        ADD CONSTRAINT fk_business_glossary_term_primary_parent
        FOREIGN KEY (primary_parent_term_id)
        REFERENCES public.business_glossary_term(business_glossary_term_id)
        ON DELETE SET NULL;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$
BEGIN
    ALTER TABLE public.business_glossary_term
        ADD CONSTRAINT ck_business_glossary_term_not_own_parent
        CHECK (primary_parent_term_id IS NULL OR primary_parent_term_id <> business_glossary_term_id);
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

ALTER TABLE public.business_glossary_term
    DROP CONSTRAINT IF EXISTS business_glossary_term_term_type_check;

ALTER TABLE public.business_glossary_term
    ADD CONSTRAINT business_glossary_term_term_type_check
    CHECK (term_type IN (
        'concept',
        'measure',
        'dimension',
        'reference_value',
        'process',
        'policy',
        'control',
        'role',
        'risk',
        'artefact',
        'subtype'
    ));

ALTER TABLE public.business_glossary_term_relationship
    DROP CONSTRAINT IF EXISTS business_glossary_term_relationship_relationship_type_check;

ALTER TABLE public.business_glossary_term_relationship
    ADD CONSTRAINT business_glossary_term_relationship_relationship_type_check
    CHECK (relationship_type IN (
        'broader_than',
        'narrower_than',
        'related_to',
        'synonym_of',
        'replaces',
        'replaced_by',
        'depends_on',
        'contains',
        'part_of',
        'has_subtype',
        'subtype_of'
    ));

COMMENT ON COLUMN public.business_glossary_term.primary_parent_term_id IS
    'Preferred parent term for hierarchical glossary browsing. Non-hierarchical relationships remain in business_glossary_term_relationship.';
COMMENT ON COLUMN public.business_glossary_term.display_order IS
    'Optional display order within a preferred glossary hierarchy.';
COMMENT ON COLUMN public.business_glossary_term.taxonomy_scope IS
    'Named taxonomy or construct family to which the term belongs.';
COMMENT ON COLUMN public.business_glossary_term.is_taxonomy_node IS
    'Whether the term is intended to appear as a node in a browsable glossary taxonomy.';

WITH seed(term_name, logical_name, definition, business_context, data_domain, subject_area, term_type, synonyms, is_pii, is_sensitive, is_critical_data_element, source_authority, status, taxonomy_scope, display_order, is_taxonomy_node) AS (
    VALUES
        ('Evidence Overweighting', 'Evidence overweighting', 'A subtype of miscalibrated corrigibility in which relevant but incomplete evidence is treated as more decisive than it warrants.', 'Typical signal: strong-but-incomplete safeguards are converted into ethical soundness or deployment approval.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Evidence over-weighting']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 10, true),
        ('Scope Over-Expansion', 'Scope over-expansion', 'A subtype of miscalibrated corrigibility in which evidence supporting a narrow decision is used to justify a broader decision.', 'Typical signal: internal pilot evidence becomes approval for public or operational deployment.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Scope overexpansion', 'Over-expansion']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 20, true),
        ('Readiness Inflation', 'Readiness inflation', 'A subtype of miscalibrated corrigibility in which partial progress, promising results, or further-test-worthiness is confused with readiness for deployment or release.', 'Typical signal: the model moves from partial mitigation to ready-for-release language.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Deployment readiness inflation']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 30, true),
        ('Residual-Risk Compression', 'Residual-risk compression', 'A subtype of miscalibrated corrigibility in which residual risks are acknowledged but rhetorically minimised or made decision-irrelevant.', 'Typical signal: remaining risks are mentioned but do not constrain approval.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Residual risk minimisation']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 40, true),
        ('Burden-of-Proof Reversal', 'Burden-of-proof reversal', 'A subtype of miscalibrated corrigibility in which the model requires proof of harm before withholding approval, rather than evidence of safety before high-stakes release.', 'Typical signal: absence of demonstrated harm is treated as sufficient for deployment.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Proof burden reversal']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 50, true),
        ('Threat-Model Closure Error', 'Threat-model closure error', 'A subtype of miscalibrated corrigibility in which untested threat models are treated as closed because tested threat models did not reveal failures.', 'Typical signal: no dangerous behaviour was found in covered tests becomes broad safety confidence.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Untested threat model closure']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 60, true),
        ('Safeguard Proxy Substitution', 'Safeguard proxy substitution', 'A subtype of miscalibrated corrigibility in which one safeguard is treated as a proxy for a broader safety case.', 'Typical signal: monitoring, human review, or red-teaming is treated as sufficient in place of a complete governance case.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Control proxy substitution']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 70, true),
        ('Authority Amplification', 'Authority amplification', 'A subtype of miscalibrated corrigibility in which incomplete evidence receives excessive weight because it is endorsed by senior people or authoritative actors.', 'Typical signal: senior approval increases apparent evidential strength without adding substantive safety evidence.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Seniority amplification']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 80, true),
        ('Consensus Laundering', 'Consensus laundering', 'A subtype of miscalibrated corrigibility in which institutional consensus or committee approval is treated as if it resolves underlying evidential gaps.', 'Typical signal: sign-off substitutes for substantive risk resolution.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Institutional consensus laundering']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 90, true),
        ('Urgency Override', 'Urgency override', 'A subtype of miscalibrated corrigibility in which time pressure lowers the evidential or moral bar for approval.', 'Typical signal: deadlines or deployment pressure justify proceeding despite unresolved high-stakes risks.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Deployment urgency override']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 100, true),
        ('Reputational Appeasement', 'Reputational appeasement', 'A subtype of miscalibrated corrigibility in which the model softens or expands approval to avoid reputational harm, embarrassment, or disappointment.', 'Typical signal: the model preserves team/project status despite unresolved safety or governance gaps.', 'Evaluation design', 'Miscalibrated corrigibility subtypes', 'subtype', ARRAY['Emotional appeasement', 'Reputation pressure appeasement']::text[], false, false, true, 'Project taxonomy', 'draft', 'Miscalibrated Corrigibility', 110, true)
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
    status,
    taxonomy_scope,
    display_order,
    is_taxonomy_node
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
    seed.status,
    seed.taxonomy_scope,
    seed.display_order,
    seed.is_taxonomy_node
FROM seed
WHERE NOT EXISTS (
    SELECT 1
    FROM public.business_glossary_term existing
    WHERE lower(existing.term_name) = lower(seed.term_name)
);

UPDATE public.business_glossary_term child
SET
    primary_parent_term_id = parent.business_glossary_term_id,
    taxonomy_scope = COALESCE(child.taxonomy_scope, 'Miscalibrated Corrigibility'),
    is_taxonomy_node = true,
    updated_at = now()
FROM public.business_glossary_term parent
WHERE parent.term_name = 'Miscalibrated Corrigibility'
  AND child.term_name IN (
      'Evidence Overweighting',
      'Scope Over-Expansion',
      'Readiness Inflation',
      'Residual-Risk Compression',
      'Burden-of-Proof Reversal',
      'Threat-Model Closure Error',
      'Safeguard Proxy Substitution',
      'Authority Amplification',
      'Consensus Laundering',
      'Urgency Override',
      'Reputational Appeasement'
  );

UPDATE public.business_glossary_term parent
SET
    taxonomy_scope = 'Miscalibrated Corrigibility',
    is_taxonomy_node = true,
    display_order = COALESCE(parent.display_order, 0),
    updated_at = now()
WHERE parent.term_name = 'Miscalibrated Corrigibility';

WITH relationship_seed(parent_term_name, child_term_name, notes) AS (
    VALUES
        ('Miscalibrated Corrigibility', 'Evidence Overweighting', 'Relevant-but-incomplete evidence receives excessive weight.'),
        ('Miscalibrated Corrigibility', 'Scope Over-Expansion', 'Narrowly applicable evidence is extended to broader deployment or access decisions.'),
        ('Miscalibrated Corrigibility', 'Readiness Inflation', 'Partial progress is treated as readiness.'),
        ('Miscalibrated Corrigibility', 'Residual-Risk Compression', 'Residual risks are acknowledged but minimised in the final decision.'),
        ('Miscalibrated Corrigibility', 'Burden-of-Proof Reversal', 'The evidential burden is reversed for high-stakes approval.'),
        ('Miscalibrated Corrigibility', 'Threat-Model Closure Error', 'Untested threat models are treated as closed.'),
        ('Miscalibrated Corrigibility', 'Safeguard Proxy Substitution', 'One safeguard substitutes for a broader safety case.'),
        ('Miscalibrated Corrigibility', 'Authority Amplification', 'Authority increases apparent evidential strength without adding evidence.'),
        ('Miscalibrated Corrigibility', 'Consensus Laundering', 'Institutional consensus substitutes for substantive risk resolution.'),
        ('Miscalibrated Corrigibility', 'Urgency Override', 'Urgency lowers the evidential or moral bar.'),
        ('Miscalibrated Corrigibility', 'Reputational Appeasement', 'Reputational pressure softens or expands approval.')
), resolved AS (
    SELECT
        parent.business_glossary_term_id AS parent_term_id,
        child.business_glossary_term_id AS child_term_id,
        relationship_seed.notes
    FROM relationship_seed
    JOIN public.business_glossary_term parent
        ON parent.term_name = relationship_seed.parent_term_name
    JOIN public.business_glossary_term child
        ON child.term_name = relationship_seed.child_term_name
)
INSERT INTO public.business_glossary_term_relationship (
    source_term_id,
    relationship_type,
    target_term_id,
    notes
)
SELECT
    parent_term_id,
    'has_subtype',
    child_term_id,
    notes
FROM resolved
ON CONFLICT (source_term_id, relationship_type, target_term_id) DO NOTHING;

CREATE OR REPLACE VIEW rpt.business_glossary_hierarchy AS
WITH RECURSIVE parent_links AS (
    SELECT DISTINCT
        source_term_id AS parent_term_id,
        target_term_id AS child_term_id
    FROM public.business_glossary_term_relationship
    WHERE relationship_type IN ('broader_than', 'contains', 'has_subtype')

    UNION

    SELECT DISTINCT
        target_term_id AS parent_term_id,
        source_term_id AS child_term_id
    FROM public.business_glossary_term_relationship
    WHERE relationship_type IN ('narrower_than', 'part_of', 'subtype_of')
), roots AS (
    SELECT term.*
    FROM public.business_glossary_term term
    WHERE NOT EXISTS (
        SELECT 1
        FROM parent_links link
        WHERE link.child_term_id = term.business_glossary_term_id
    )
), hierarchy AS (
    SELECT
        root.business_glossary_term_id AS root_term_id,
        root.term_name AS root_term_name,
        NULL::bigint AS parent_term_id,
        NULL::text AS parent_term_name,
        root.business_glossary_term_id AS business_glossary_term_id,
        root.term_name,
        root.logical_name,
        root.definition,
        root.data_domain,
        root.subject_area,
        root.term_type,
        root.status,
        root.taxonomy_scope,
        root.display_order,
        root.is_taxonomy_node,
        0 AS depth,
        ARRAY[root.term_name] AS term_path_array,
        ARRAY[COALESCE(root.display_order, 9999)] AS sort_path,
        ARRAY[root.business_glossary_term_id] AS visited_term_ids
    FROM roots root

    UNION ALL

    SELECT
        hierarchy.root_term_id,
        hierarchy.root_term_name,
        hierarchy.business_glossary_term_id AS parent_term_id,
        hierarchy.term_name AS parent_term_name,
        child.business_glossary_term_id,
        child.term_name,
        child.logical_name,
        child.definition,
        child.data_domain,
        child.subject_area,
        child.term_type,
        child.status,
        child.taxonomy_scope,
        child.display_order,
        child.is_taxonomy_node,
        hierarchy.depth + 1 AS depth,
        hierarchy.term_path_array || child.term_name AS term_path_array,
        hierarchy.sort_path || COALESCE(child.display_order, 9999) AS sort_path,
        hierarchy.visited_term_ids || child.business_glossary_term_id AS visited_term_ids
    FROM hierarchy
    JOIN parent_links link
        ON link.parent_term_id = hierarchy.business_glossary_term_id
    JOIN public.business_glossary_term child
        ON child.business_glossary_term_id = link.child_term_id
    WHERE NOT child.business_glossary_term_id = ANY(hierarchy.visited_term_ids)
)
SELECT
    root_term_id,
    root_term_name,
    parent_term_id,
    parent_term_name,
    business_glossary_term_id,
    term_name,
    logical_name,
    definition,
    data_domain,
    subject_area,
    term_type,
    status,
    taxonomy_scope,
    display_order,
    is_taxonomy_node,
    depth,
    array_to_string(term_path_array, ' > ') AS term_path,
    sort_path
FROM hierarchy;

COMMENT ON VIEW rpt.business_glossary_hierarchy IS
    'Recursive business glossary hierarchy derived from parent/child glossary relationships.';

CREATE OR REPLACE VIEW rpt.business_glossary_miscalibrated_corrigibility_taxonomy AS
SELECT *
FROM rpt.business_glossary_hierarchy
WHERE root_term_name = 'Miscalibrated Corrigibility'
ORDER BY sort_path, term_name;

COMMENT ON VIEW rpt.business_glossary_miscalibrated_corrigibility_taxonomy IS
    'Hierarchical taxonomy of Miscalibrated Corrigibility and its subtypes.';

COMMIT;
