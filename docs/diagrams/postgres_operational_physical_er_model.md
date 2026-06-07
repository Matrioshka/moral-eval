# PostgreSQL operational physical ER model

This diagram shows the current operational tables added on top of the original provenance/import layer. The operational tables remain in the `public` schema for now. Original import-shaped tables are included only where they are referenced for lineage or foreign-key anchoring.

The diagram intentionally distinguishes exact reconstructed turns from `unresolved_legacy_prompt` placeholder turns. Placeholder turns are operational anchors for imported responses whose exact prompt text was not reconstructed; they are not exact source prompt text.

```mermaid
erDiagram
    dataset ||--o{ dataset_case : contains
    source_file ||--o{ dataset_case : sourced_from
    source_file ||--o{ case_turn : sourced_from
    source_file ||--o{ response : sourced_from
    source_file ||--o{ score_event : sourced_from

    dataset_case ||--|| eval_case : normalises_to
    scenario ||--o{ eval_case : frames
    moral_domain ||--o{ eval_case : classifies
    eval_case ||--o{ case_turn : has
    case_turn ||--o{ case_turn : parent_of
    turn_type ||--o{ case_turn : categorises
    pressure_type ||--o{ case_turn : annotates
    evidence_quality ||--o{ case_turn : annotates
    case_intervention ||--o{ case_turn : source_followup

    model_run ||--o{ model_response : produced_legacy
    dataset_case ||--o{ model_response : legacy_case
    model_response ||--o| response : normalises_to
    model_run ||--o{ response : produced
    case_turn ||--o{ response : answered_by

    response ||--o{ score_event : scored_by
    scorer ||--o{ score_event : performs
    rubric ||--o{ score_event : applies
    failure_class ||--o{ score_event : labels
    manual_score ||--o| score_event : legacy_manual_source
    deterministic_score ||--o| score_event : legacy_deterministic_source

    dataset {
        BIGINT dataset_id PK
        TEXT dataset_version
        TEXT dataset_name
        TEXT description
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    dataset_case {
        BIGINT case_pk PK
        BIGINT dataset_id FK
        TEXT sample_id
        TEXT case_id
        TEXT source_item_id
        TEXT scenario
        BIGINT scenario_id FK
        TEXT moral_domain
        TEXT initial_judgement
        TEXT case_origin
        BOOLEAN is_canonical_dataset_item
        BIGINT source_file_id FK
        JSONB raw_record
    }

    source_file {
        BIGINT source_file_id PK
        TEXT file_path
        TEXT file_kind
        TEXT content_hash
        INTEGER record_count
        TIMESTAMPTZ indexed_at
    }

    moral_domain {
        INTEGER moral_domain_id PK
        TEXT slug UK
        TEXT name
        TEXT description
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    scenario {
        BIGINT scenario_id PK
        TEXT scenario_hash UK
        TEXT name
        TEXT scenario_text
        TEXT description
        TEXT source_name
        TEXT source_reference
        TEXT source_uri
        BIGINT source_file_id FK
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    eval_case {
        BIGINT eval_case_id PK
        BIGINT dataset_case_pk FK_UK
        BIGINT dataset_id FK
        TEXT sample_id
        TEXT case_id
        TEXT source_item_id
        BIGINT scenario_id FK
        INTEGER moral_domain_id FK
        TEXT initial_judgement
        BIGINT source_file_id FK
        JSONB raw_metadata
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    turn_type {
        INTEGER turn_type_id PK
        TEXT slug UK
        TEXT name
        TEXT description
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    pressure_type {
        INTEGER pressure_type_id PK
        TEXT slug UK
        TEXT name
        TEXT description
        TEXT pressure_family
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    evidence_quality {
        INTEGER evidence_quality_id PK
        TEXT slug UK
        TEXT name
        TEXT description
        INTEGER strength_rank
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    case_intervention {
        BIGINT intervention_id PK
        BIGINT case_pk FK
        TEXT user_followup
        TEXT pressure_type
        INTEGER evidence_quality_id FK
        TEXT followup_strength
        TEXT pressure_source
        TEXT pressure_mechanism
        TEXT pressure_legitimacy
        TEXT pressure_escalation_stage
        TEXT pressure_target
        TEXT conflict_type
        JSONB raw_metadata
    }

    case_turn {
        BIGINT case_turn_id PK
        BIGINT eval_case_id FK
        BIGINT parent_turn_id FK
        INTEGER turn_index
        TEXT turn_type
        INTEGER turn_type_id FK
        TEXT turn_text
        TEXT intervention_role
        TEXT pressure_type
        INTEGER pressure_type_id FK
        INTEGER evidence_quality_id FK
        BIGINT source_case_pk FK
        BIGINT source_intervention_id FK
        TEXT source_column
        JSONB raw_metadata
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    model_run {
        BIGINT run_id PK
        BIGINT dataset_id FK
        TEXT run_label
        TEXT model_name
        TEXT prompt_style
        TIMESTAMPTZ run_timestamp
        BIGINT source_file_id FK
        JSONB raw_metadata
    }

    model_response {
        BIGINT response_id PK
        BIGINT run_id FK
        BIGINT case_pk FK
        TEXT sample_id
        TEXT case_id
        TEXT source_item_id
        TEXT dataset_version
        TEXT prompt_style
        TEXT raw_response
        BIGINT source_file_id FK
        INTEGER source_row
        JSONB raw_row
    }

    response {
        BIGINT response_id PK
        BIGINT case_turn_id FK
        BIGINT run_id FK
        BIGINT legacy_model_response_id FK_UK
        TEXT response_role
        TEXT response_text
        BIGINT source_file_id FK
        INTEGER source_row
        JSONB raw_metadata
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    scorer {
        BIGINT scorer_id PK
        TEXT scorer_type
        TEXT name
        TEXT model_name
        TEXT description
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    rubric {
        BIGINT rubric_id PK
        TEXT rubric_name UK
        TEXT description
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    failure_class {
        BIGINT failure_class_id PK
        TEXT slug UK
        TEXT name
        TEXT description
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }

    manual_score {
        BIGINT manual_score_id PK
        BIGINT response_id FK
        BIGINT rubric_id FK
        BIGINT primary_failure_class_id FK
        NUMERIC score_0_to_3
        TEXT action
        TEXT confidence
        TEXT notes
        BIGINT source_file_id FK
        INTEGER source_row
        JSONB raw_row
    }

    deterministic_score {
        BIGINT deterministic_score_id PK
        BIGINT response_id FK
        TEXT scorer_name
        TEXT score_value
        TEXT answer
        TEXT explanation
        JSONB metadata
        BIGINT source_file_id FK
        INTEGER source_row
        JSONB raw_row
    }

    score_event {
        BIGINT score_event_id PK
        BIGINT response_id FK
        BIGINT scorer_id FK
        BIGINT rubric_id FK
        BIGINT failure_class_id FK
        NUMERIC score
        TEXT label
        TEXT rationale
        TEXT notes
        NUMERIC confidence
        TEXT confidence_label
        BIGINT source_file_id FK
        INTEGER source_row
        BIGINT legacy_manual_score_id FK_UK
        BIGINT legacy_deterministic_score_id FK_UK
        JSONB raw_metadata
        TIMESTAMPTZ created_at
        TIMESTAMPTZ updated_at
    }
```

## Operational core

The intended operational chain is:

```text
moral_domain + scenario + dataset_case
  -> eval_case
  -> case_turn
  -> response
  -> score_event
```

`model_run` stays separate from `response`, so model metadata is not repeated in every response row. Reporting views should join `response -> model_run` when model name, run label, or prompt style is needed.

## Important modelling choices

`eval_case` is the normalised operational case table. It currently keeps a one-to-one link to `dataset_case` through `dataset_case_pk`, because the existing project remains file/dataset-first.

`case_turn` is the general turn table. It supports scenario turns, follow-up turns, and future multi-round/branched turns through `parent_turn_id`. It now has lookup IDs for `turn_type` and `pressure_type`, while the old text columns remain temporarily for compatibility.

`response` is separate from `case_turn` so multiple model responses can be attached to the same turn. It links back to `model_response` through `legacy_model_response_id` for provenance.

`score_event` is intentionally response-level. It should not absorb run-level summaries, tuple-consistency summaries, or deterministic summary rows unless those can be tied to a specific operational `response`.

`unresolved_legacy_prompt` is an explicit placeholder turn type. It is used only when a valid imported response exists but the exact prompt/turn text was not reconstructed from source artefacts. It should be excluded from exact-prompt reporting unless clearly labelled.

## Likely next refinements

The old text columns `case_turn.turn_type` and `case_turn.pressure_type` can eventually be removed or retained only as raw import fields once all views/scripts use `turn_type_id` and `pressure_type_id`.

The original provenance/import tables may later move to a `raw` schema. Reporting views may later move to an `rpt` schema. The operational tables can reasonably remain in `public`.

A future `diagnostic_event`, `run_metric`, or `summary_metric` table is probably better than weakening `score_event` to absorb non-response-level deterministic summaries.
