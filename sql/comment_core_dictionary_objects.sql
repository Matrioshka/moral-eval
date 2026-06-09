-- Seed native PostgreSQL COMMENT definitions for the core dictionary and
-- evaluation-reporting objects.
--
-- PostgreSQL comments are the standard source of object definitions. The
-- rpt.data_dictionary view reads these comments live from pg_catalog and joins
-- them to curated governance metadata in public.data_dictionary_entry.

BEGIN;

COMMENT ON SCHEMA raw IS
    'Imported source-shaped provenance and landing schema. Raw tables preserve source artefact shape and lineage.';
COMMENT ON SCHEMA public IS
    'Operational schema for curated datasets, cases, turns, responses, scores, runs, lookup tables, and app-facing metadata.';
COMMENT ON SCHEMA rpt IS
    'Reporting schema for stable query surfaces and web-interface views over operational and provenance data.';

COMMENT ON TABLE raw.source_file IS
    'Source artefact registry with path, kind, hash, size, record count, and ingestion timestamps.';
COMMENT ON TABLE raw.dataset_case IS
    'Imported dataset and case rows before operational normalisation into eval_case, scenario, case_turn, and case_expectation.';
COMMENT ON TABLE raw.case_intervention IS
    'Imported follow-up or intervention metadata before pressure-turn normalisation.';
COMMENT ON TABLE raw.expected_behaviour IS
    'Imported expected-behaviour metadata before promotion to public.case_expectation.';
COMMENT ON TABLE raw.model_response IS
    'Imported model response rows before promotion to public.response.';
COMMENT ON TABLE raw.structured_decision_tuple IS
    'Imported structured extraction rows before promotion to public.response_structured_decision.';
COMMENT ON TABLE raw.manual_score IS
    'Imported manual audit rows before promotion to public.score_event.';
COMMENT ON TABLE raw.deterministic_score IS
    'Imported deterministic scorer rows before promotion to public.score_event.';

COMMENT ON TABLE public.dataset IS
    'Curated dataset or dataset version used by evaluation cases and model runs.';
COMMENT ON TABLE public.run IS
    'Model/eval run that produced responses. Distinct from experiment_pipeline_run orchestration executions.';
COMMENT ON TABLE public.eval_case IS
    'Curated evaluation case within a dataset.';
COMMENT ON TABLE public.case_turn IS
    'Prompt, scenario, or pressure/intervention turn belonging to an eval_case.';
COMMENT ON TABLE public.case_expectation IS
    'Operational case-level expectation and acceptable-bound metadata promoted from raw.expected_behaviour.';
COMMENT ON TABLE public.response IS
    'Model response to one case_turn in one run.';
COMMENT ON TABLE public.response_structured_decision IS
    'Operational structured extraction attached to public.response, promoted from raw.structured_decision_tuple.';
COMMENT ON TABLE public.score_event IS
    'Score, label, rationale, and failure-class judgement attached to a response.';
COMMENT ON TABLE public.data_dictionary_entry IS
    'Curated DMBOK-style governance and business metadata for database objects. Technical metadata and definitions are read live from PostgreSQL catalog comments.';

COMMENT ON COLUMN public.dataset.dataset_id IS 'Primary key for a curated dataset/version.';
COMMENT ON COLUMN public.dataset.dataset_version IS 'Stable dataset version identifier.';
COMMENT ON COLUMN public.dataset.dataset_family IS 'Dataset family or scenario-design family.';
COMMENT ON COLUMN public.dataset.source_file_id IS 'Source artefact that introduced or last identified this dataset.';

COMMENT ON COLUMN public.run.run_id IS 'Primary key for a model/eval run.';
COMMENT ON COLUMN public.run.run_label IS 'Human-readable or source-derived identifier for a model/eval run.';
COMMENT ON COLUMN public.run.model_name IS 'Model name used for the run, when known or inferred.';
COMMENT ON COLUMN public.run.provider IS 'Model provider or execution provider, when known.';
COMMENT ON COLUMN public.run.dataset_id IS 'Dataset declared for the run. Should match the dataset of responses through case_turn and eval_case.';
COMMENT ON COLUMN public.run.dataset_version IS 'Dataset version recorded for the run when a dataset_id is unavailable or inherited from source artefacts.';
COMMENT ON COLUMN public.run.prompt_style IS 'Prompt style used for the run, when known.';
COMMENT ON COLUMN public.run.run_timestamp IS 'Timestamp associated with the model/eval run when known.';
COMMENT ON COLUMN public.run.source_file_id IS 'Source artefact that introduced or identified this run.';

COMMENT ON COLUMN public.eval_case.eval_case_id IS 'Primary key for a curated evaluation case.';
COMMENT ON COLUMN public.eval_case.dataset_case_pk IS 'Lineage key back to raw.dataset_case.case_pk.';
COMMENT ON COLUMN public.eval_case.dataset_id IS 'Dataset containing this evaluation case.';
COMMENT ON COLUMN public.eval_case.sample_id IS 'Stable sample identifier for the evaluation case.';
COMMENT ON COLUMN public.eval_case.case_id IS 'Case identifier when distinct from sample_id.';
COMMENT ON COLUMN public.eval_case.source_item_id IS 'Original source item identifier when provided by the dataset.';
COMMENT ON COLUMN public.eval_case.scenario_id IS 'Scenario text associated with the case.';
COMMENT ON COLUMN public.eval_case.initial_judgement IS 'Initial judgement or baseline judgement text associated with the case.';

COMMENT ON COLUMN public.case_turn.case_turn_id IS 'Primary key for a case turn.';
COMMENT ON COLUMN public.case_turn.eval_case_id IS 'Evaluation case containing this turn.';
COMMENT ON COLUMN public.case_turn.parent_turn_id IS 'Parent turn for multi-stage pressure or follow-up structures.';
COMMENT ON COLUMN public.case_turn.turn_index IS 'Order of the turn within its eval_case.';
COMMENT ON COLUMN public.case_turn.turn_type IS 'Turn type label such as scenario, user_followup, or pressure turn.';
COMMENT ON COLUMN public.case_turn.turn_text IS 'Text of the scenario, prompt, intervention, or pressure turn.';
COMMENT ON COLUMN public.case_turn.intervention_role IS 'Role or source of an intervention/follow-up turn, when applicable.';
COMMENT ON COLUMN public.case_turn.pressure_type IS 'Pressure type label captured from source data, when applicable.';
COMMENT ON COLUMN public.case_turn.turn_type_id IS 'Normalised turn type lookup reference.';
COMMENT ON COLUMN public.case_turn.pressure_type_id IS 'Normalised pressure type lookup reference.';
COMMENT ON COLUMN public.case_turn.evidence_quality_id IS 'Normalised evidence quality lookup reference.';

COMMENT ON COLUMN public.case_expectation.case_expectation_id IS 'Primary key for operational case expectation metadata.';
COMMENT ON COLUMN public.case_expectation.eval_case_id IS 'Evaluation case to which the expectation metadata applies.';
COMMENT ON COLUMN public.case_expectation.expected_behaviour IS 'Concise expected behaviour or target judgement for the case.';
COMMENT ON COLUMN public.case_expectation.ideal_behaviour IS 'Ideal behaviour for the model under the scenario and any justified evidence.';
COMMENT ON COLUMN public.case_expectation.target_scope IS 'Target decision scope for scope-control style cases.';
COMMENT ON COLUMN public.case_expectation.minimum_acceptable_scope IS 'Minimum acceptable scope before the response is too conservative.';
COMMENT ON COLUMN public.case_expectation.maximum_acceptable_scope IS 'Maximum acceptable scope before the response is too permissive.';
COMMENT ON COLUMN public.case_expectation.target_release_scope IS 'Target release scope for release-governance style cases.';
COMMENT ON COLUMN public.case_expectation.minimum_acceptable_release_scope IS 'Minimum acceptable release scope before the response is too conservative.';
COMMENT ON COLUMN public.case_expectation.maximum_acceptable_release_scope IS 'Maximum acceptable release scope before the response is too permissive.';
COMMENT ON COLUMN public.case_expectation.target_access IS 'Target access tuple for schema-v2.1 style release/access decisions.';
COMMENT ON COLUMN public.case_expectation.minimum_acceptable_access IS 'Minimum acceptable access tuple before the response is too conservative.';
COMMENT ON COLUMN public.case_expectation.maximum_acceptable_access IS 'Most permissive acceptable access tuple before the response should be treated as over-expansive.';
COMMENT ON COLUMN public.case_expectation.failure_modes IS 'Known failure modes relevant to grading this case.';
COMMENT ON COLUMN public.case_expectation.pressure_trap IS 'Pressure or reassurance trap that the model should resist unless evidence justifies updating.';

COMMENT ON COLUMN public.response.response_id IS 'Primary key for an operational response.';
COMMENT ON COLUMN public.response.case_turn_id IS 'Case turn answered by this response.';
COMMENT ON COLUMN public.response.run_id IS 'Model/eval run that produced this response.';
COMMENT ON COLUMN public.response.legacy_model_response_id IS 'Lineage key back to raw.model_response.response_id.';
COMMENT ON COLUMN public.response.response_role IS 'Role of the response message, usually assistant.';
COMMENT ON COLUMN public.response.response_text IS 'Model response text.';
COMMENT ON COLUMN public.response.source_file_id IS 'Source artefact that introduced this response.';
COMMENT ON COLUMN public.response.source_row IS 'Source row number within the source artefact, when applicable.';

COMMENT ON COLUMN public.response_structured_decision.response_structured_decision_id IS 'Primary key for a structured decision extraction attached to a response.';
COMMENT ON COLUMN public.response_structured_decision.response_id IS 'Operational response to which this structured decision is attached.';
COMMENT ON COLUMN public.response_structured_decision.tuple_schema IS 'Schema contract used to interpret the structured extraction row.';
COMMENT ON COLUMN public.response_structured_decision.access_intent IS 'Extracted intended use or access intent approved or implied by the response.';
COMMENT ON COLUMN public.response_structured_decision.access_population IS 'Extracted population or audience allowed by the response.';
COMMENT ON COLUMN public.response_structured_decision.access_modality IS 'Extracted access mode, channel, or deployment modality.';
COMMENT ON COLUMN public.response_structured_decision.operational_status IS 'Extracted operational or deployment status implied by the response.';
COMMENT ON COLUMN public.response_structured_decision.real_world_exposure IS 'Extracted degree of real-world exposure implied by the response.';
COMMENT ON COLUMN public.response_structured_decision.externalisation_level IS 'Extracted degree of external release or exposure implied by the response.';
COMMENT ON COLUMN public.response_structured_decision.raw_tuple IS 'Lossless structured extraction payload used to audit or recover fields beyond the promoted operational columns.';
COMMENT ON COLUMN public.response_structured_decision.source_file_id IS 'Source artefact that introduced this structured extraction.';
COMMENT ON COLUMN public.response_structured_decision.legacy_structured_decision_tuple_id IS 'Lineage key back to raw.structured_decision_tuple.tuple_id.';

COMMENT ON COLUMN public.score_event.score_event_id IS 'Primary key for a response-level score event.';
COMMENT ON COLUMN public.score_event.response_id IS 'Response being scored.';
COMMENT ON COLUMN public.score_event.scorer_id IS 'Scorer or scoring process that produced this score event.';
COMMENT ON COLUMN public.score_event.score IS 'Numeric score assigned by a manual, deterministic, or future scorer.';
COMMENT ON COLUMN public.score_event.label IS 'Score label or action label assigned by the scorer.';
COMMENT ON COLUMN public.score_event.failure_class_id IS 'Failure class assigned by the scorer, when applicable.';
COMMENT ON COLUMN public.score_event.rationale IS 'Reasoning or grading rationale for the score.';

COMMENT ON VIEW rpt.run_summary IS
    'One row per public.run with response, case, structured-decision, and score counts for run list pages.';
COMMENT ON VIEW rpt.run_detail IS
    'One row per response within a public.run, with case, turn, expectation, structured decision, and score rollup fields.';
COMMENT ON VIEW rpt.case_run_trace IS
    'Reporting trace view over public operational dataset/run/expectation/extraction metadata plus raw response, score, and source provenance rows.';
COMMENT ON VIEW rpt.case_run_trace_reporting IS
    'Reporting-safe subset of rpt.case_run_trace, excluding smoke, summary, expansion-candidate, and rewrite-candidate artefacts.';
COMMENT ON VIEW rpt.score_linkage_status IS
    'Read-only diagnostic view showing whether each raw.manual_score row is linked to a response-level score_event, and why unlinked rows remain outside score_event.';
COMMENT ON VIEW rpt.technical_data_dictionary IS
    'Live technical metadata for raw, public, and rpt schemas, tables, views, and columns, sourced from PostgreSQL catalog tables and native comments.';
COMMENT ON VIEW rpt.data_dictionary IS
    'Joined data dictionary for web/interface use. Technical metadata and definitions come live from PostgreSQL; governance metadata comes from public.data_dictionary_entry.';
COMMENT ON VIEW rpt.data_dictionary_missing_comment IS
    'Database objects in raw, public, or rpt without native PostgreSQL COMMENT definitions.';
COMMENT ON VIEW rpt.data_dictionary_governance_gap IS
    'Objects missing curated governance metadata such as logical name, PII review, review status, or data domain.';

COMMIT;
