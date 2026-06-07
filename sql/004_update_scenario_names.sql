-- Update scenario.name to short, relevant labels derived from canonical case metadata.
--
-- The exact scenario text remains in scenario.scenario_text and dataset_case.scenario.
-- This script only improves the human-readable scenario.name field for browsing,
-- diagrams, and case-card exports.

with scenario_context as (
    select
        s.scenario_id,
        s.scenario_text,
        min(dc.moral_domain) filter (
            where dc.moral_domain is not null
              and length(trim(dc.moral_domain::text)) > 0
        ) as moral_domain,
        min(dc.case_id) filter (
            where dc.case_id is not null
              and length(trim(dc.case_id::text)) > 0
              and coalesce(dc.is_canonical_dataset_item, false) = true
        ) as canonical_case_id,
        min(dc.case_id) filter (
            where dc.case_id is not null
              and length(trim(dc.case_id::text)) > 0
        ) as any_case_id,
        min(dc.source_item_id) filter (
            where dc.source_item_id is not null
              and length(trim(dc.source_item_id::text)) > 0
        ) as source_item_id
    from scenario s
    join dataset_case dc
      on dc.scenario_id = s.scenario_id
    group by s.scenario_id, s.scenario_text
), chosen_identifier as (
    select
        scenario_id,
        scenario_text,
        coalesce(canonical_case_id, any_case_id, source_item_id, moral_domain, 'scenario') as identifier
    from scenario_context
), cleaned as (
    select
        scenario_id,
        scenario_text,
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(lower(identifier), '^v[0-9]+_', ''),
                        '_manual_scores$', ''
                    ),
                    '_outputs$', ''
                ),
                '_structured_access_decision(_v[0-9_]+)?$', ''
            ),
            '_+', ' ', 'g'
        ) as cleaned_identifier
    from chosen_identifier
), titled as (
    select
        scenario_id,
        scenario_text,
        regexp_replace(
            replace(
                replace(
                    replace(
                        replace(
                            replace(
                                replace(
                                    replace(initcap(cleaned_identifier), ' Ai ', ' AI '),
                                    'Ai ', 'AI '
                                ),
                                ' Api ', ' API '
                            ),
                            'Api ', 'API '
                        ),
                        ' Pii ', ' PII '
                    ),
                    'Pii ', 'PII '
                ),
                ' Gpt ', ' GPT '
            ),
            '\s+', ' ', 'g'
        ) as generated_name
    from cleaned
), final_names as (
    select
        scenario_id,
        case
            when length(trim(generated_name)) between 8 and 80 then trim(generated_name)
            else left(regexp_replace(scenario_text, '\s+', ' ', 'g'), 80)
        end as scenario_name
    from titled
)
update scenario s
set
    name = final_names.scenario_name,
    updated_at = now()
from final_names
where s.scenario_id = final_names.scenario_id
  and s.name is distinct from final_names.scenario_name;

-- Quick review query.
select scenario_id, name
from scenario
order by name;
