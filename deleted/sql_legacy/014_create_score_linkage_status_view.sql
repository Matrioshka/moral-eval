-- Create a read-only status view for manual_score linkage coverage.
--
-- This view does not backfill or mutate score_event rows. It makes the current
-- linkage state explicit so the remaining legacy manual-score ambiguity can be
-- inspected from SQL without weakening score_event's response-level invariant.

BEGIN;

DROP VIEW IF EXISTS score_linkage_status;

CREATE VIEW score_linkage_status AS
SELECT
    ms.manual_score_id,
    ms.response_id AS legacy_model_response_id,
    se.score_event_id,
    se.response_id AS operational_response_id,
    (se.score_event_id IS NOT NULL) AS is_linked_to_score_event,
    CASE
        WHEN se.score_event_id IS NOT NULL THEN 'linked_to_score_event'
        WHEN mr.response_id IS NULL THEN 'unlinked_missing_legacy_model_response'
        WHEN mr.raw_response IS NULL OR length(trim(mr.raw_response)) = 0 THEN 'unlinked_blank_legacy_response_stub'
        WHEN r.response_id IS NULL THEN 'unlinked_valid_legacy_response_missing_operational_response'
        ELSE 'unlinked_other'
    END AS linkage_status,
    CASE
        WHEN mr.raw_response IS NOT NULL AND length(trim(mr.raw_response)) > 0 THEN true
        ELSE false
    END AS legacy_model_response_has_text,
    CASE
        WHEN r.response_id IS NOT NULL THEN true
        ELSE false
    END AS has_operational_response,
    mr.case_pk,
    mr.sample_id,
    mr.case_id,
    mr.source_item_id,
    mr.dataset_version,
    mr.prompt_style AS response_prompt_style,
    run.run_id,
    run.run_label,
    run.model_name,
    run.prompt_style AS run_prompt_style,
    ms.score_0_to_3 AS manual_score,
    fc.name AS failure_class,
    ms.confidence AS manual_confidence,
    ms.action AS manual_action,
    ms.notes AS manual_notes,
    sf_manual.file_path AS manual_score_source_path,
    ms.source_row AS manual_score_source_row,
    sf_response.file_path AS legacy_response_source_path,
    mr.source_row AS legacy_response_source_row,
    sf_operational.file_path AS operational_response_source_path,
    r.source_row AS operational_response_source_row,
    ms.created_at AS manual_score_created_at,
    se.created_at AS score_event_created_at
FROM manual_score ms
LEFT JOIN model_response mr
    ON mr.response_id = ms.response_id
LEFT JOIN model_run run
    ON run.run_id = mr.run_id
LEFT JOIN response r
    ON r.legacy_model_response_id = mr.response_id
LEFT JOIN score_event se
    ON se.legacy_manual_score_id = ms.manual_score_id
LEFT JOIN failure_class fc
    ON fc.failure_class_id = ms.primary_failure_class_id
LEFT JOIN source_file sf_manual
    ON sf_manual.source_file_id = ms.source_file_id
LEFT JOIN source_file sf_response
    ON sf_response.source_file_id = mr.source_file_id
LEFT JOIN source_file sf_operational
    ON sf_operational.source_file_id = r.source_file_id;

COMMENT ON VIEW score_linkage_status IS
    'Read-only diagnostic view showing whether each manual_score row is linked to a response-level score_event, and why unlinked rows remain outside score_event.';

COMMIT;
