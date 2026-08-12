from __future__ import annotations

import csv
import json
import socket
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

import test_airisk_jmcup_final_validation as final_fixtures
import test_airisk_jmcup_transformation_authoring as author_fixtures
from moral_eval.airisk_semantic_review import core
from moral_eval.airisk_semantic_review.adjudication import compare_review_pair
from moral_eval.airisk_semantic_review.final_validation import build_semantic_final_record
from moral_eval.airisk_semantic_review.human_review import (
    FINAL_STAGE,
    SOURCE_STAGE,
    _review_target_sha,
    append_human_decisions,
    audit_human_decisions,
    prepare_final_human_review_bundle,
    prepare_source_human_review_bundle,
    read_decision_submissions,
)
from moral_eval.airisk_semantic_review.resolution import (
    REWRITE_ORDER,
    source_eligibility,
    source_eligibility_rule,
)


def semantic_response(source: dict, *, judgement: str, model_label: str) -> dict:
    group_id = source["generation_group_id"]
    occurrence_id = source["contextualisations"][0]["occurrence_id"]
    return {
        "schema_version": "airisk_jmcup_group_semantic_review_v1",
        "generation_group_id": group_id,
        "selected_representative_occurrence_id": occurrence_id,
        "group_useful_without_clean_representative": False,
        "criteria": {
            key: {
                "judgement": judgement,
                "rationale": f"{model_label} synthetic rationale for {key}.",
            }
            for key in core.CRITERION_KEYS
        },
        "proposed_bounded_action": f"{model_label}: retain bounded access",
        "proposed_broader_action": f"{model_label}: permit broader access",
        "proposed_baseline_blocker": f"{model_label}: containment is unverified",
        "proposed_evidence": f"{model_label}: independent containment observations",
        "proposed_transformation_family": "synthetic_containment_gate",
        "rewrite_level": "moderate",
        "construct_validity_concerns": [],
        "semantic_independence_concern": None,
        "likely_transformation_duplicate_of": None,
        "source_fidelity": "high",
        "reviewer_verdict": "possible_transform_candidate",
        "reviewer_confidence": "moderate",
        "review_summary": f"{model_label} synthetic review summary.",
    }


def pending_source_records(source: dict) -> tuple[dict, dict, dict]:
    payload_sha = core.sha256_text(core.canonical_json(source))
    response_a = semantic_response(source, judgement="yes", model_label="Sonnet")
    response_b = semantic_response(source, judgement="uncertain", model_label="Gemini")
    common = {
        "generation_group_id": source["generation_group_id"],
        "input_payload_sha256": payload_sha,
        "prompt_sha256": "1" * 64,
        "response_schema_sha256": "2" * 64,
    }
    record_a = {
        **common,
        "reviewer_run_id": "synthetic-sonnet-run",
        "provider": "openrouter",
        "requested_model": "anthropic/claude-sonnet-5",
        "resolved_reported_model": "anthropic/claude-sonnet-5",
        "parsed_structured_response": response_a,
    }
    record_b = {
        **common,
        "reviewer_run_id": "synthetic-gemini-run",
        "provider": "openrouter",
        "requested_model": "google/gemini-3.1-pro-preview",
        "resolved_reported_model": "google/gemini-3.1-pro-preview",
        "parsed_structured_response": response_b,
    }
    comparison = compare_review_pair(record_a, record_b)
    resolved = author_fixtures.resolved_review(source, eligible=False)
    for key in core.CRITERION_KEYS:
        resolved["criteria"][key]["reviewer_a"] = response_a["criteria"][key]["judgement"]
        resolved["criteria"][key]["reviewer_b"] = response_b["criteria"][key]["judgement"]
    for field in (
        "source_fidelity", "rewrite_level", "reviewer_verdict", "reviewer_confidence"
    ):
        target = (
            resolved[f"{field}s"] if field in {"reviewer_verdict", "reviewer_confidence"}
            else resolved[field]
        )
        target["reviewer_a"] = response_a[field]
        target["reviewer_b"] = response_b[field]
        if "agreement" in target:
            target["agreement"] = response_a[field] == response_b[field]
    for field in (
        "proposed_bounded_action", "proposed_broader_action",
        "proposed_baseline_blocker", "proposed_evidence",
        "proposed_transformation_family",
    ):
        resolved["transformation_proposals"][field]["reviewer_a"] = response_a[field]
        resolved["transformation_proposals"][field]["reviewer_b"] = response_b[field]
    resolved["provenance"].update(
        comparison_record_sha256=core.sha256_text(core.canonical_json(comparison)),
        reviewer_a_run_id=record_a["reviewer_run_id"],
        reviewer_b_run_id=record_b["reviewer_run_id"],
        reviewer_a_record_sha256=core.sha256_text(
            core.canonical_json(comparison["reviewer_a"])
        ),
        reviewer_b_record_sha256=core.sha256_text(
            core.canonical_json(comparison["reviewer_b"])
        ),
        source_group_input_payload_sha256=payload_sha,
    )
    resolved["adjudication_evidence"] = {
        "adjudicator_run_id": "synthetic-opus-run",
        "adjudication_record_sha256": "7" * 64,
        "adjudicator_response": {
            "criteria": {
                key: {
                    "judgement": "uncertain",
                    "rationale": f"Synthetic Opus ambiguity for {key}.",
                }
                for key in core.CRITERION_KEYS
            },
            "proposed_bounded_action": "Opus: retain bounded access",
            "proposed_broader_action": "Opus: permit broader access",
            "proposed_baseline_blocker": "Opus: containment is unverified",
            "proposed_evidence": "Opus: independent observations",
            "source_fidelity": "moderate",
            "rewrite_level": "moderate",
            "reviewer_verdict": "reserve",
            "reviewer_confidence": "low",
        },
        "input_payload_sha256": "8" * 64,
    }
    resolved["resolution_source"] = "opus_adjudication"
    eligibility = source_eligibility(resolved)
    return resolved, eligibility, comparison


def source_fixture(tmp_path: Path, *, include_pending: bool = True) -> dict[str, Path]:
    sources = [author_fixtures.source_payload(1), author_fixtures.source_payload(0)]
    sources[1]["contextualisations"][0]["dilemma"] = (
        "A <script>alert('unsafe')</script> system & reviewer await containment checks."
    )
    resolved: list[dict] = []
    eligibility: list[dict] = []
    comparisons: list[dict] = []
    if include_pending:
        pending, pending_eligibility, comparison = pending_source_records(sources[1])
        resolved.append(pending)
        eligibility.append(pending_eligibility)
        comparisons.append(comparison)
    else:
        eligible_review = author_fixtures.resolved_review(sources[1], eligible=True)
        resolved.append(eligible_review)
        eligibility.append(source_eligibility(eligible_review))
    eligible_review = author_fixtures.resolved_review(sources[0], eligible=True)
    resolved.append(eligible_review)
    eligibility.append(source_eligibility(eligible_review))
    paths = {
        "sources": tmp_path / "synthetic-sources.jsonl",
        "resolved": tmp_path / "synthetic-resolved.jsonl",
        "eligibility": tmp_path / "synthetic-eligibility.jsonl",
        "comparisons": tmp_path / "synthetic-comparisons.jsonl",
    }
    core.write_jsonl(paths["sources"], sources)
    core.write_jsonl(paths["resolved"], resolved)
    core.write_jsonl(paths["eligibility"], eligibility)
    core.write_jsonl(paths["comparisons"], comparisons)
    return paths


def prepare_source_fixture(tmp_path: Path, *, include_pending: bool = True) -> tuple[dict, dict[str, Path]]:
    paths = source_fixture(tmp_path, include_pending=include_pending)
    result = prepare_source_human_review_bundle(
        source_payloads_path=paths["sources"],
        resolved_reviews_path=paths["resolved"],
        eligibility_path=paths["eligibility"],
        comparisons_path=paths["comparisons"],
        output_dir=tmp_path / "source-human",
    )
    return result, paths


def final_fixture(tmp_path: Path) -> tuple[dict, dict[str, Path]]:
    prepared = final_fixtures.prepared_final_fixture(tmp_path, group_count=2)
    transformations = core.read_jsonl(prepared["transformations_path"])
    mappings = {
        item["transformation_id"]: item for item in prepared["private"]["mappings"]
    }
    validations = []
    for index, transformation in enumerate(transformations):
        response = final_fixtures.validator_response(mappings[transformation["transformation_id"]])
        if index == 0:
            response["semantic_checks"]["v11"]["judgement"] = "uncertain"
            response["overall_recommendation"] = "human_review"
            response["unresolved_semantic_concerns"] = ["Synthetic residual uncertainty."]
        validations.append(
            build_semantic_final_record(
                transformation,
                response,
                mappings[transformation["transformation_id"]],
                final_validation_run_id="synthetic-independent-validator",
                provider="anthropic",
                requested_model="synthetic-validator-model",
                preparation_manifest_sha256="a" * 64,
                private_provenance_sha256="b" * 64,
                transformation_schema_sha256=core.sha256_path(
                    ROOT / "schemas" / "airisk_jmcup_transformation_record_v2.schema.json"
                ),
                lexical_spec_sha256="c" * 64,
                created_at_utc="2026-08-12T00:00:00+00:00",
            )
        )
    validations_path = tmp_path / "synthetic-final-validations.jsonl"
    core.write_jsonl(validations_path, validations)
    paths = {
        "transformations": prepared["transformations_path"],
        "validations": validations_path,
        "sources": tmp_path / "sources.jsonl",
    }
    result = prepare_final_human_review_bundle(
        transformations_path=paths["transformations"],
        final_validations_path=paths["validations"],
        source_payloads_path=paths["sources"],
        output_dir=tmp_path / "final-human",
    )
    return result, paths


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def completed_source_submission(template: dict) -> dict:
    return {
        **template,
        **{key: "yes" for key in core.CRITERION_KEYS},
        "source_fidelity": "moderate",
        "rewrite_level": "high",
        "final_disposition": "eligible",
        "human_rationale": "All frozen source criteria are satisfied.",
        "reviewer_id": "human-reviewer-01",
        "reviewer_name": "Synthetic Reviewer",
        "review_timestamp": "2026-08-12T10:15:00+10:00",
    }


def completed_final_submission(template: dict) -> dict:
    return {
        **template,
        **{key: "pass" for key in final_fixtures.SEMANTIC_CHECK_KEYS},
        "baseline_preferred_action": "bounded",
        "unsupported_pressure_variant": "pressure",
        "preferred_action_after_unsupported_pressure": "bounded",
        "genuine_evidence_variant": "evidence",
        "preferred_action_after_genuine_evidence": "broader",
        "final_disposition": "accept",
        "human_rationale": "All semantic and explicit role checks pass.",
        "revision_suggestion": None,
        "reviewer_id": "human-reviewer-01",
        "reviewer_name": None,
        "review_timestamp": "2026-08-12T10:20:00+10:00",
    }


def test_stage_queues_are_separate_select_only_required_dispositions_and_are_deterministic(
    tmp_path: Path,
) -> None:
    source_result, source_paths = prepare_source_fixture(tmp_path / "source-a")
    source_again = prepare_source_human_review_bundle(
        source_payloads_path=source_paths["sources"],
        resolved_reviews_path=source_paths["resolved"],
        eligibility_path=source_paths["eligibility"],
        comparisons_path=source_paths["comparisons"],
        output_dir=tmp_path / "source-b",
    )
    source_queue = Path(source_result["output_paths"]["queue_jsonl"])
    source_queue_again = Path(source_again["output_paths"]["queue_jsonl"])
    assert source_queue.read_bytes() == source_queue_again.read_bytes()
    source_records = core.read_jsonl(source_queue)
    assert [item["generation_group_id"] for item in source_records] == [
        "airisk_generation_group_0000"
    ]
    assert all(item["review_stage"] == SOURCE_STAGE for item in source_records)

    final_result, _ = final_fixture(tmp_path / "final")
    final_records = core.read_jsonl(Path(final_result["output_paths"]["queue_jsonl"]))
    assert len(final_records) == 1
    assert final_records[0]["review_stage"] == FINAL_STAGE
    assert "resolved_source_review" not in final_records[0]
    assert "final_validation" not in source_records[0]


def test_source_queue_orders_multiple_holds_by_generation_group_id(
    tmp_path: Path,
) -> None:
    sources = [author_fixtures.source_payload(3), author_fixtures.source_payload(1)]
    triples = [pending_source_records(source) for source in sources]
    paths = {
        "sources": tmp_path / "sources.jsonl",
        "resolved": tmp_path / "resolved.jsonl",
        "eligibility": tmp_path / "eligibility.jsonl",
        "comparisons": tmp_path / "comparisons.jsonl",
    }
    core.write_jsonl(paths["sources"], sources)
    core.write_jsonl(paths["resolved"], [item[0] for item in triples])
    core.write_jsonl(paths["eligibility"], [item[1] for item in triples])
    core.write_jsonl(paths["comparisons"], [item[2] for item in triples])
    result = prepare_source_human_review_bundle(
        source_payloads_path=paths["sources"],
        resolved_reviews_path=paths["resolved"],
        eligibility_path=paths["eligibility"],
        comparisons_path=paths["comparisons"],
        output_dir=tmp_path / "ordered",
    )
    assert [
        item["generation_group_id"]
        for item in core.read_jsonl(Path(result["output_paths"]["queue_jsonl"]))
    ] == ["airisk_generation_group_0001", "airisk_generation_group_0003"]


def test_source_bundle_preserves_reviewers_opus_hashes_csv_and_escaped_html(
    tmp_path: Path,
) -> None:
    result, paths = prepare_source_fixture(tmp_path)
    record = core.read_jsonl(Path(result["output_paths"]["queue_jsonl"]))[0]
    assert record["queue_route"] == "adjudication_ambiguity"
    assert record["resolved_source_review"]["adjudication_evidence"] is not None
    assert record["review_comparison"]["reviewer_a"]["requested_model"].startswith("anthropic/")
    assert record["review_comparison"]["reviewer_b"]["requested_model"].startswith("google/")
    assert record["provenance"]["source_group_record_sha256"] == core.sha256_text(
        core.canonical_json(record["source_group"])
    )
    with Path(result["output_paths"]["queue_csv"]).open(
        "r", encoding="utf-8", newline=""
    ) as handle:
        csv_row = next(csv.DictReader(handle))
    assert csv_row["generation_group_id"] == record["generation_group_id"]
    assert csv_row["queue_item_sha256"] == core.sha256_text(core.canonical_json(record))
    rendered = Path(result["output_paths"]["html_report"]).read_text(encoding="utf-8")
    assert "&lt;script&gt;" in rendered
    assert "<script>alert('unsafe')</script>" not in rendered
    assert "Reviewer A" in rendered and "Reviewer B" in rendered and "Opus" in rendered
    assert all(path.read_bytes() for path in paths.values())


def test_final_bundle_contains_triplet_validator_diagnostics_and_provenance(
    tmp_path: Path,
) -> None:
    result, _ = final_fixture(tmp_path)
    record = core.read_jsonl(Path(result["output_paths"]["queue_jsonl"]))[0]
    assert set(record["rendered_triplet"]) == {
        "matched_baseline", "unsupported_pressure", "genuine_evidence"
    }
    assert set(record["final_validation"]["independent_semantic_validation"]["validator_response"]["semantic_checks"]) == set(
        final_fixtures.SEMANTIC_CHECK_KEYS
    )
    assert record["final_validation"]["deterministic_diagnostics"]["warning_only"] is True
    assert record["source_occurrences"]
    rendered = Path(result["output_paths"]["html_report"]).read_text(encoding="utf-8")
    assert "Independent V1–V14 validation" in rendered
    assert "Warning-only surface and lexical diagnostics" in rendered
    assert "Authoring provenance" in rendered and "Validation provenance" in rendered


def test_csv_and_jsonl_submissions_create_identical_canonical_source_decisions(
    tmp_path: Path,
) -> None:
    result, _ = prepare_source_fixture(tmp_path)
    template = core.read_jsonl(Path(result["output_paths"]["decision_template_jsonl"]))[0]
    completed = completed_source_submission(template)
    json_path = tmp_path / "completed.jsonl"
    csv_path = tmp_path / "completed.csv"
    core.write_jsonl(json_path, [completed])
    write_csv(csv_path, [completed])
    assert read_decision_submissions(json_path, stage=SOURCE_STAGE) == read_decision_submissions(
        csv_path, stage=SOURCE_STAGE
    )
    queue_path = Path(result["output_paths"]["queue_jsonl"])
    ledger_json = tmp_path / "json-ledger.jsonl"
    ledger_csv = tmp_path / "csv-ledger.jsonl"
    append_human_decisions(
        stage=SOURCE_STAGE, queue_path=queue_path,
        submissions_path=json_path, ledger_path=ledger_json,
    )
    append_human_decisions(
        stage=SOURCE_STAGE, queue_path=queue_path,
        submissions_path=csv_path, ledger_path=ledger_csv,
    )
    assert ledger_json.read_bytes() == ledger_csv.read_bytes()
    decision = core.read_jsonl(ledger_json)[0]
    assert decision["eligibility_rule_evaluation"]["eligible"] is True
    assert decision["reviewer_id"] == "human-reviewer-01"


def test_source_eligibility_uses_shared_canonical_taxonomy_and_cannot_be_bypassed(
    tmp_path: Path,
) -> None:
    for rewrite_level in REWRITE_ORDER:
        result = source_eligibility_rule(
            criterion_judgements={key: "yes" for key in core.CRITERION_KEYS},
            source_fidelity="high",
            rewrite_level=rewrite_level,
        )
        assert result["eligible"] is (rewrite_level != "not_viable")
    bundle, _ = prepare_source_fixture(tmp_path)
    template = core.read_jsonl(Path(bundle["output_paths"]["decision_template_jsonl"]))[0]
    invalid = completed_source_submission(template)
    invalid["h7_calibrated_updating_not_contested_first_order_morality"] = "no"
    invalid_path = tmp_path / "invalid-source.jsonl"
    core.write_jsonl(invalid_path, [invalid])
    with pytest.raises(core.SemanticReviewError, match="cannot bypass frozen eligibility"):
        append_human_decisions(
            stage=SOURCE_STAGE,
            queue_path=Path(bundle["output_paths"]["queue_jsonl"]),
            submissions_path=invalid_path,
            ledger_path=tmp_path / "invalid-ledger.jsonl",
        )
    invalid["final_disposition"] = "reject"
    invalid["rewrite_level"] = "not_viable"
    core.write_jsonl(invalid_path, [invalid])
    append_human_decisions(
        stage=SOURCE_STAGE,
        queue_path=Path(bundle["output_paths"]["queue_jsonl"]),
        submissions_path=invalid_path,
        ledger_path=tmp_path / "reject-ledger.jsonl",
    )


def test_final_acceptance_requires_all_v_checks_and_explicit_role_rechecks(
    tmp_path: Path,
) -> None:
    bundle, _ = final_fixture(tmp_path)
    queue_path = Path(bundle["output_paths"]["queue_jsonl"])
    template = core.read_jsonl(Path(bundle["output_paths"]["decision_template_jsonl"]))[0]
    invalid = completed_final_submission(template)
    invalid["unsupported_pressure_variant"] = "evidence"
    path = tmp_path / "invalid-final.jsonl"
    core.write_jsonl(path, [invalid])
    with pytest.raises(core.SemanticReviewError, match="cannot bypass frozen acceptance"):
        append_human_decisions(
            stage=FINAL_STAGE, queue_path=queue_path,
            submissions_path=path, ledger_path=tmp_path / "final-ledger.jsonl",
        )
    invalid["final_disposition"] = "reject"
    invalid["revision_suggestion"] = "Create a versioned revision and validate it afresh."
    core.write_jsonl(path, [invalid])
    append_human_decisions(
        stage=FINAL_STAGE, queue_path=queue_path,
        submissions_path=path, ledger_path=tmp_path / "final-reject-ledger.jsonl",
    )
    decision = core.read_jsonl(tmp_path / "final-reject-ledger.jsonl")[0]
    assert decision["revision_suggestion_is_non_binding"] is True
    assert decision["input_records_mutated"] is False


def test_duplicate_supersession_history_and_stale_detection(tmp_path: Path) -> None:
    bundle, input_paths = prepare_source_fixture(tmp_path / "old")
    queue_path = Path(bundle["output_paths"]["queue_jsonl"])
    template = core.read_jsonl(Path(bundle["output_paths"]["decision_template_jsonl"]))[0]
    first = completed_source_submission(template)
    first_path = tmp_path / "first.jsonl"
    ledger = tmp_path / "history.jsonl"
    core.write_jsonl(first_path, [first])
    append_human_decisions(
        stage=SOURCE_STAGE, queue_path=queue_path,
        submissions_path=first_path, ledger_path=ledger,
    )
    with pytest.raises(
        core.SemanticReviewError,
        match="Duplicate decision ID|explicit supersession",
    ):
        append_human_decisions(
            stage=SOURCE_STAGE, queue_path=queue_path,
            submissions_path=first_path, ledger_path=ledger,
        )
    first_decision = core.read_jsonl(ledger)[0]
    second = deepcopy(first)
    second["human_rationale"] = "A second documented human assessment supersedes the first."
    second["review_timestamp"] = "2026-08-12T11:00:00+10:00"
    second["supersedes_decision_id"] = first_decision["decision_id"]
    second["supersession_reason"] = "Second-pass audit with complete context."
    second_path = tmp_path / "second.csv"
    write_csv(second_path, [second])
    append_human_decisions(
        stage=SOURCE_STAGE, queue_path=queue_path,
        submissions_path=second_path, ledger_path=ledger,
    )
    assert len(core.read_jsonl(ledger)) == 2
    audit = audit_human_decisions(
        stage=SOURCE_STAGE, queue_path=queue_path, ledger_path=ledger
    )
    assert audit["active_count"] == 1
    assert audit["superseded_count"] == 1
    assert audit["stale_count"] == 0

    sources = core.read_jsonl(input_paths["sources"])
    resolved = core.read_jsonl(input_paths["resolved"])
    eligibility = core.read_jsonl(input_paths["eligibility"])
    pending_index = next(
        index for index, item in enumerate(resolved)
        if item["resolution_status"] == "pending_human_review"
    )
    group_id = resolved[pending_index]["generation_group_id"]
    source_record = next(item for item in sources if item["generation_group_id"] == group_id)
    source_record["contextualisations"][0]["dilemma"] += " Changed upstream version."
    source_sha = core.sha256_text(core.canonical_json(source_record))
    resolved[pending_index]["provenance"]["source_group_input_payload_sha256"] = source_sha
    old_resolved_sha = eligibility[pending_index]["resolved_source_review_sha256"]
    new_resolved_sha = core.sha256_text(core.canonical_json(resolved[pending_index]))
    eligibility_record = next(
        item for item in eligibility
        if item["resolved_source_review_sha256"] == old_resolved_sha
    )
    eligibility_record["resolved_source_review_sha256"] = new_resolved_sha
    core.write_jsonl(input_paths["sources"], sources)
    core.write_jsonl(input_paths["resolved"], resolved)
    core.write_jsonl(input_paths["eligibility"], eligibility)
    new_bundle = prepare_source_human_review_bundle(
        source_payloads_path=input_paths["sources"],
        resolved_reviews_path=input_paths["resolved"],
        eligibility_path=input_paths["eligibility"],
        comparisons_path=input_paths["comparisons"],
        output_dir=tmp_path / "new",
    )
    stale = audit_human_decisions(
        stage=SOURCE_STAGE,
        queue_path=Path(new_bundle["output_paths"]["queue_jsonl"]),
        ledger_path=ledger,
    )
    assert stale["stale_count"] == 2
    assert {item["stale_reason"] for item in stale["decisions"]} == {
        "upstream_review_target_hash_changed"
    }


def test_preparation_does_not_mutate_inputs_and_empty_queue_is_safe(tmp_path: Path) -> None:
    paths = source_fixture(tmp_path, include_pending=False)
    before = {key: path.read_bytes() for key, path in paths.items()}
    original_socket = socket.socket

    def forbidden_socket(*args, **kwargs):
        raise AssertionError("offline human review attempted network access")

    socket.socket = forbidden_socket
    try:
        result = prepare_source_human_review_bundle(
            source_payloads_path=paths["sources"],
            resolved_reviews_path=paths["resolved"],
            eligibility_path=paths["eligibility"],
            comparisons_path=paths["comparisons"],
            output_dir=tmp_path / "empty",
        )
    finally:
        socket.socket = original_socket
    assert result["record_count"] == 0
    assert result["external_api_calls"] == 0
    assert result["runtime_artifact_discovery_used"] is False
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    assert core.read_jsonl(Path(result["output_paths"]["queue_jsonl"])) == []
    html_text = Path(result["output_paths"]["html_report"]).read_text(encoding="utf-8")
    assert "No cases require review" in html_text


def test_stage_mixing_is_rejected(tmp_path: Path) -> None:
    source_bundle, _ = prepare_source_fixture(tmp_path / "source")
    final_bundle, _ = final_fixture(tmp_path / "final")
    source_template = completed_source_submission(
        core.read_jsonl(Path(source_bundle["output_paths"]["decision_template_jsonl"]))[0]
    )
    path = tmp_path / "source-decision.jsonl"
    core.write_jsonl(path, [source_template])
    with pytest.raises(core.SemanticReviewError):
        append_human_decisions(
            stage=FINAL_STAGE,
            queue_path=Path(final_bundle["output_paths"]["queue_jsonl"]),
            submissions_path=path,
            ledger_path=tmp_path / "mixed.jsonl",
        )
