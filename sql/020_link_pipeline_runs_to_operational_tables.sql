-- Link Inspect/pipeline provenance tables to the promoted operational tables.
--
-- public.experiment_pipeline_run records orchestration state and raw artefact
-- paths. public.run/response/eval_case records the promoted operational model
-- run and response graph. This migration materialises the bridge between them.

BEGIN;

CREATE SCHEMA IF NOT EXISTS rpt;

ALTER TABLE public.experiment_pipeline_run
    ADD COLUMN IF NOT EXISTS operational_run_id bigint REFERENCES public.run(run_id);

ALTER TABLE public.inspect_log_sample
    ADD COLUMN IF NOT EXISTS eval_case_id bigint REFERENCES public.eval_case(eval_case_id),
    ADD COLUMN IF NOT EXISTS response_id bigint REFERENCES public.response(response_id);

CREATE INDEX IF NOT EXISTS ix_experiment_pipeline_run_operational_run
    ON public.experiment_pipeline_run(operational_run_id);

CREATE INDEX IF NOT EXISTS ix_inspect_log_sample_eval_case
    ON public.inspect_log_sample(eval_case_id);

CREATE INDEX IF NOT EXISTS ix_inspect_log_sample_response
    ON public.inspect_log_sample(response_id);

-- Link pipeline runs to public.run. The strongest deterministic bridge is the
-- exported outputs CSV path because raw.model_run.run_label is derived from the
-- same repo-relative path without extension.
WITH normalised_pipeline_path AS (
    SELECT
        epr.experiment_pipeline_run_id,
        replace(epr.outputs_csv_path, '\', '/') AS normalised_outputs_csv_path
    FROM public.experiment_pipeline_run epr
    WHERE epr.outputs_csv_path IS NOT NULL
), candidate_run_label AS (
    SELECT
        experiment_pipeline_run_id,
        regexp_replace(
            CASE
                WHEN normalised_outputs_csv_path ~ '/tmp/' THEN regexp_replace(normalised_outputs_csv_path, '^.*/tmp/', 'tmp/')
                WHEN normalised_outputs_csv_path ~ '/docs/' THEN regexp_replace(normalised_outputs_csv_path, '^.*/docs/', 'docs/')
                WHEN normalised_outputs_csv_path ~ '/artefacts/' THEN regexp_replace(normalised_outputs_csv_path, '^.*/artefacts/', 'artefacts/')
                WHEN normalised_outputs_csv_path ~ '/results/' THEN regexp_replace(normalised_outputs_csv_path, '^.*/results/', 'results/')
                ELSE normalised_outputs_csv_path
            END,
            '\.[^./]+$',
            ''
        ) AS run_label
    FROM normalised_pipeline_path
), matched_run AS (
    SELECT DISTINCT ON (candidate.experiment_pipeline_run_id)
        candidate.experiment_pipeline_run_id,
        run.run_id
    FROM candidate_run_label candidate
    JOIN public.run run
      ON run.run_label = candidate.run_label
    ORDER BY candidate.experiment_pipeline_run_id, run.run_id DESC
)
UPDATE public.experiment_pipeline_run epr
SET
    operational_run_id = matched_run.run_id,
    updated_at = now()
FROM matched_run
WHERE epr.experiment_pipeline_run_id = matched_run.experiment_pipeline_run_id
  AND epr.operational_run_id IS DISTINCT FROM matched_run.run_id;

-- Logs backfilled without an exported CSV can still be linked by exact final
-- response text for samples that are already promoted into public.response.
WITH response_match_candidate AS (
    SELECT
        epr.experiment_pipeline_run_id,
        resp.run_id,
        count(DISTINCT ils.inspect_log_sample_id) AS exact_response_matches,
        count(DISTINCT ec.eval_case_id) AS case_matches,
        row_number() OVER (
            PARTITION BY epr.experiment_pipeline_run_id
            ORDER BY
                count(DISTINCT ils.inspect_log_sample_id) DESC,
                count(DISTINCT ec.eval_case_id) DESC,
                resp.run_id DESC
        ) AS candidate_rank
    FROM public.experiment_pipeline_run epr
    JOIN public.inspect_log_sample ils
      ON ils.experiment_pipeline_run_id = epr.experiment_pipeline_run_id
    JOIN public.dataset dataset
      ON dataset.dataset_version = COALESCE(ils.dataset_version, epr.dataset_version)
    JOIN public.eval_case ec
      ON ec.dataset_id = dataset.dataset_id
     AND ec.sample_id = ils.sample_id
    JOIN public.case_turn ct
      ON ct.eval_case_id = ec.eval_case_id
    JOIN public.response resp
      ON resp.case_turn_id = ct.case_turn_id
    JOIN public.run run
      ON run.run_id = resp.run_id
    WHERE epr.operational_run_id IS NULL
      AND nullif(btrim(ils.final_response), '') IS NOT NULL
      AND nullif(btrim(resp.response_text), '') IS NOT NULL
      AND btrim(ils.final_response) = btrim(resp.response_text)
      AND (
          epr.dataset_version IS NULL
          OR run.dataset_version IS NULL
          OR run.dataset_version = epr.dataset_version
          OR dataset.dataset_version = epr.dataset_version
      )
    GROUP BY epr.experiment_pipeline_run_id, resp.run_id
), matched_by_response AS (
    SELECT
        experiment_pipeline_run_id,
        run_id
    FROM response_match_candidate
    WHERE candidate_rank = 1
      AND exact_response_matches > 0
)
UPDATE public.experiment_pipeline_run epr
SET
    operational_run_id = matched_by_response.run_id,
    updated_at = now()
FROM matched_by_response
WHERE epr.experiment_pipeline_run_id = matched_by_response.experiment_pipeline_run_id
  AND epr.operational_run_id IS DISTINCT FROM matched_by_response.run_id;

-- Conservative metadata fallback: if dataset/model/prompt identifies exactly
-- one public.run, link it. Ambiguous groups are intentionally left unlinked.
WITH metadata_candidate AS (
    SELECT
        epr.experiment_pipeline_run_id,
        min(run.run_id) AS run_id,
        count(DISTINCT run.run_id) AS candidate_run_count
    FROM public.experiment_pipeline_run epr
    JOIN public.run run
      ON run.dataset_version = epr.dataset_version
     AND (
          epr.prompt_style IS NULL
          OR run.prompt_style IS NULL
          OR run.prompt_style = epr.prompt_style
     )
     AND (
          epr.answer_model->>'model' IS NULL
          OR run.model_name IS NULL
          OR run.model_name = epr.answer_model->>'model'
     )
    WHERE epr.operational_run_id IS NULL
      AND epr.pipeline_run_key LIKE 'inspect-log-backfill-%'
      AND epr.dataset_version IS NOT NULL
    GROUP BY epr.experiment_pipeline_run_id
), unique_metadata_match AS (
    SELECT experiment_pipeline_run_id, run_id
    FROM metadata_candidate
    WHERE candidate_run_count = 1
)
UPDATE public.experiment_pipeline_run epr
SET
    operational_run_id = unique_metadata_match.run_id,
    updated_at = now()
FROM unique_metadata_match
WHERE epr.experiment_pipeline_run_id = unique_metadata_match.experiment_pipeline_run_id
  AND epr.operational_run_id IS DISTINCT FROM unique_metadata_match.run_id;

-- Link Inspect samples to operational eval cases via dataset version + sample id.
WITH matched_case AS (
    SELECT DISTINCT ON (ils.inspect_log_sample_id)
        ils.inspect_log_sample_id,
        ec.eval_case_id
    FROM public.inspect_log_sample ils
    JOIN public.dataset dataset
      ON dataset.dataset_version = ils.dataset_version
    JOIN public.eval_case ec
      ON ec.dataset_id = dataset.dataset_id
     AND ec.sample_id = ils.sample_id
    ORDER BY ils.inspect_log_sample_id, ec.eval_case_id DESC
)
UPDATE public.inspect_log_sample ils
SET
    eval_case_id = matched_case.eval_case_id,
    updated_at = now()
FROM matched_case
WHERE ils.inspect_log_sample_id = matched_case.inspect_log_sample_id
  AND ils.eval_case_id IS DISTINCT FROM matched_case.eval_case_id;

-- Link Inspect samples to the matching operational response for the same
-- promoted run + eval case. Prefer exact final-response text matches; otherwise
-- use the latest response attached to that case within the operational run.
WITH response_candidate AS (
    SELECT
        ils.inspect_log_sample_id,
        resp.response_id,
        row_number() OVER (
            PARTITION BY ils.inspect_log_sample_id
            ORDER BY
                CASE
                    WHEN nullif(btrim(ils.final_response), '') IS NOT NULL
                     AND nullif(btrim(resp.response_text), '') IS NOT NULL
                     AND btrim(ils.final_response) = btrim(resp.response_text)
                    THEN 0
                    ELSE 1
                END,
                ct.turn_index DESC NULLS LAST,
                resp.response_id DESC
        ) AS candidate_rank
    FROM public.inspect_log_sample ils
    JOIN public.experiment_pipeline_run epr
      ON epr.experiment_pipeline_run_id = ils.experiment_pipeline_run_id
    JOIN public.response resp
      ON resp.run_id = epr.operational_run_id
    JOIN public.case_turn ct
      ON ct.case_turn_id = resp.case_turn_id
     AND ct.eval_case_id = ils.eval_case_id
    WHERE epr.operational_run_id IS NOT NULL
      AND ils.eval_case_id IS NOT NULL
), matched_response AS (
    SELECT inspect_log_sample_id, response_id
    FROM response_candidate
    WHERE candidate_rank = 1
)
UPDATE public.inspect_log_sample ils
SET
    response_id = matched_response.response_id,
    updated_at = now()
FROM matched_response
WHERE ils.inspect_log_sample_id = matched_response.inspect_log_sample_id
  AND ils.response_id IS DISTINCT FROM matched_response.response_id;

DROP VIEW IF EXISTS rpt.pipeline_operational_linkage;

CREATE VIEW rpt.pipeline_operational_linkage AS
WITH pipeline_sample_counts AS (
    SELECT
        experiment_pipeline_run_id,
        count(*) AS inspect_sample_count,
        count(eval_case_id) AS linked_eval_case_count,
        count(response_id) AS linked_response_count
    FROM public.inspect_log_sample
    GROUP BY experiment_pipeline_run_id
), duplicate_log_sha AS (
    SELECT
        eval_log_sha256,
        count(*) AS duplicate_sha_row_count
    FROM public.experiment_pipeline_run
    WHERE eval_log_sha256 IS NOT NULL
    GROUP BY eval_log_sha256
)
SELECT
    epr.experiment_pipeline_run_id,
    epr.pipeline_run_key,
    epr.experiment_slug,
    epr.status,
    epr.dataset_version AS pipeline_dataset_version,
    epr.outputs_csv_path,
    epr.eval_log_path,
    epr.eval_log_sha256,
    epr.original_eval_log_path,
    epr.operational_run_id,
    epr.operational_run_id AS run_id,
    run.run_label,
    run.model_name,
    run.prompt_style,
    COALESCE(psc.inspect_sample_count, 0) AS inspect_sample_count,
    COALESCE(psc.linked_eval_case_count, 0) AS linked_eval_case_count,
    COALESCE(psc.linked_response_count, 0) AS linked_response_count,
    COALESCE(dls.duplicate_sha_row_count, 0) AS duplicate_sha_row_count,
    COALESCE(dls.duplicate_sha_row_count, 0) > 1 AS is_duplicate_log_sha,
    ils.inspect_log_sample_id,
    ils.sample_id,
    ils.dataset_version AS sample_dataset_version,
    ils.eval_case_id,
    ils.response_id,
    (epr.operational_run_id IS NOT NULL) AS is_pipeline_run_linked,
    (ils.eval_case_id IS NOT NULL) AS is_sample_case_linked,
    (ils.response_id IS NOT NULL) AS is_sample_response_linked,
    CASE
        WHEN COALESCE(dls.duplicate_sha_row_count, 0) > 1 THEN 'duplicate_log_sha'
        WHEN epr.dataset_version IS NULL THEN 'missing_pipeline_dataset_version'
        WHEN COALESCE(psc.inspect_sample_count, 0) = 0 THEN 'no_inspect_samples'
        WHEN epr.operational_run_id IS NULL THEN 'missing_operational_run'
        WHEN ils.inspect_log_sample_id IS NULL THEN NULL
        WHEN ils.dataset_version IS NULL OR length(trim(ils.dataset_version)) = 0 THEN 'missing_sample_dataset_version'
        WHEN ils.eval_case_id IS NULL THEN 'missing_eval_case'
        WHEN ils.final_response IS NULL OR length(trim(ils.final_response)) = 0 THEN 'missing_final_response'
        WHEN ils.response_id IS NULL THEN 'missing_response'
        ELSE NULL
    END AS linkage_skip_reason
FROM public.experiment_pipeline_run epr
LEFT JOIN public.run run
  ON run.run_id = epr.operational_run_id
LEFT JOIN pipeline_sample_counts psc
  ON psc.experiment_pipeline_run_id = epr.experiment_pipeline_run_id
LEFT JOIN duplicate_log_sha dls
  ON dls.eval_log_sha256 = epr.eval_log_sha256
LEFT JOIN public.inspect_log_sample ils
  ON ils.experiment_pipeline_run_id = epr.experiment_pipeline_run_id;

COMMENT ON COLUMN public.experiment_pipeline_run.operational_run_id IS
    'Nullable bridge to public.run after pipeline outputs have been ingested and promoted into operational tables.';

COMMENT ON COLUMN public.inspect_log_sample.eval_case_id IS
    'Nullable bridge to public.eval_case matched from dataset_version and sample_id.';

COMMENT ON COLUMN public.inspect_log_sample.response_id IS
    'Nullable bridge to the promoted public.response for this Inspect sample, preferring exact final-response text matches.';

COMMENT ON VIEW rpt.pipeline_operational_linkage IS
    'Diagnostic bridge view showing how pipeline/Inspect provenance rows link to operational run, case, and response rows, including duplicate-log and skip-reason diagnostics.';

COMMIT;
