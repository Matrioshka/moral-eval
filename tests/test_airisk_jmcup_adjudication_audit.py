from __future__ import annotations

import json
import socket
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

import test_airisk_jmcup_semantic_review as fixtures
from moral_eval.airisk_semantic_review import adjudication_execution, core
from moral_eval.airisk_semantic_review.adjudication_audit import (
    audit_adjudicator_run,
)
from moral_eval.airisk_semantic_review.core import SemanticReviewError
from moral_eval.airisk_semantic_review.human_review import (
    prepare_source_human_review_bundle,
)
from moral_eval.airisk_semantic_review.resolution import (
    merge_resolved_source_reviews,
)


MODEL = "anthropic/claude-opus-5"
RUN_ID = "openrouter-opus-audit-fixture"
MODEL_SETTINGS = {"temperature": 0.0, "max_output_tokens": 8192}


def _clone_payload(payload: dict[str, Any], ordinal: int) -> dict[str, Any]:
    result = deepcopy(payload)
    group_id = f"airisk_generation_group_{ordinal:04d}"
    result["generation_group_id"] = group_id
    result["source_group"]["generation_group_id"] = group_id
    result["reviewer_a_response"]["generation_group_id"] = group_id
    result["reviewer_b_response"]["generation_group_id"] = group_id
    return result


def _response(payload: dict[str, Any]) -> dict[str, Any]:
    result = fixtures.opus_response_for(
        payload,
        {
            "criterion_comparison": {
                key: {
                    "reviewer_a": payload["reviewer_a_response"]["criteria"][key][
                        "judgement"
                    ],
                    "disagreement": (
                        payload["reviewer_a_response"]["criteria"][key]["judgement"]
                        != payload["reviewer_b_response"]["criteria"][key]["judgement"]
                    ),
                }
                for key in core.CRITERION_KEYS
            }
        },
        disposition="candidate",
    )
    return result


def _usage(cost: float = 0.01) -> dict[str, Any]:
    return {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "cost": cost,
        "prompt_tokens_details": {"cached_tokens": 10},
        "completion_tokens_details": {"reasoning_tokens": 5},
    }


def _normalised(usage: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "reasoning_tokens": usage["completion_tokens_details"]["reasoning_tokens"],
        "cached_tokens": usage["prompt_tokens_details"]["cached_tokens"],
        "total_tokens": usage["total_tokens"],
        "cost": usage["cost"],
    }


def _attempt(
    status: str, *, cost: float = 0.01, signal: str | None = None
) -> dict[str, Any]:
    usage = _usage(cost)
    metadata = (
        {"provider": "openrouter", "native_code": signal or "SAFETY"}
        if status in {"blocked", "refused"}
        else None
    )
    return {
        "attempt_number": 1,
        "timestamp_utc": "2026-08-12T00:00:00+00:00",
        "status": status,
        "resolved_reported_model": MODEL,
        "provider_request_id": f"request-{status}",
        "provider_generation_id": f"generation-{status}",
        "actual_routed_provider": "Anthropic",
        "provider_usage": usage,
        "normalised_usage": _normalised(usage),
        "output_termination": None,
        "provider_block_metadata": metadata,
        "validation_errors": [],
        "error_type": None,
        "error_message": None,
        "retry_delay_seconds": None,
    }


def _record(
    payload: dict[str, Any],
    status: str,
    *,
    cost: float = 0.01,
    response: dict[str, Any] | None = None,
    signal: str | None = None,
) -> dict[str, Any]:
    parsed = response if status == "completed" else None
    attempt = _attempt(status, cost=cost, signal=signal)
    metadata = attempt["provider_block_metadata"]
    usage = attempt["provider_usage"]
    return {
        "record_schema_version": "airisk_jmcup_adjudicator_run_record_v1",
        "adjudicator_run_id": RUN_ID,
        "generation_group_id": payload["generation_group_id"],
        "provider": "openrouter",
        "requested_model": MODEL,
        "resolved_reported_model": MODEL,
        "provider_request_id": attempt["provider_request_id"],
        "provider_generation_id": attempt["provider_generation_id"],
        "actual_routed_provider": "Anthropic",
        "provider_usage": usage,
        "aggregate_usage": {
            **_normalised(usage),
            "reported_attempt_counts": {
                key: 1 for key in _normalised(usage)
            },
        },
        "output_termination": None,
        "provider_reasoning": None,
        "prompt_version": "airisk_jmcup_opus_adjudication_v1",
        "prompt_sha256": core.sha256_path(
            adjudication_execution.DEFAULT_ADJUDICATION_PROMPT_PATH
        ),
        "response_schema_sha256": core.sha256_path(
            adjudication_execution.DEFAULT_ADJUDICATION_RESPONSE_SCHEMA_PATH
        ),
        "input_payload_sha256": core.sha256_text(core.canonical_json(payload)),
        "status": status,
        "raw_response": parsed if parsed is not None else {"blocked": metadata},
        "raw_response_text": json.dumps(parsed) if parsed is not None else None,
        "parsed_structured_response": parsed,
        "provider_block_metadata": metadata,
        "timestamp_utc": "2026-08-12T00:00:00+00:00",
        "model_settings": MODEL_SETTINGS,
        "retry_information": {
            "max_retries": 0,
            "attempt_count": 1,
            "attempts": [attempt],
        },
        "validation_errors": [],
        "semantic_truth_deterministically_established": False,
    }


def _write_audit_inputs(
    tmp_path: Path,
    payloads: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, Path]:
    paths = {
        "preparation": tmp_path / "preparation.json",
        "payloads": tmp_path / "payloads.jsonl",
        "run_manifest": tmp_path / "run_manifest.json",
        "records": tmp_path / "adjudications.jsonl",
        "audit": tmp_path / "audit.json",
    }
    core.write_jsonl(paths["payloads"], payloads)
    selected_ids = [payload["generation_group_id"] for payload in payloads]
    preparation = {
        "schema_version": "airisk_jmcup_opus_adjudication_manifest_v1",
        "selected_group_count": len(selected_ids),
        "selected_group_ids": selected_ids,
        "payloads_sha256": core.sha256_path(paths["payloads"]),
    }
    paths["preparation"].write_text(
        json.dumps(preparation, indent=2) + "\n", encoding="utf-8"
    )
    run_manifest = {
        "schema_version": "airisk_jmcup_adjudicator_run_manifest_v1",
        "created_at_utc": "2026-08-12T00:00:00+00:00",
        "adjudicator_run_id": RUN_ID,
        "provider": "openrouter",
        "requested_model": MODEL,
        "selected_generation_group_ids": selected_ids,
        "prompt_version": "airisk_jmcup_opus_adjudication_v1",
        "prompt_sha256": core.sha256_path(
            adjudication_execution.DEFAULT_ADJUDICATION_PROMPT_PATH
        ),
        "input_schema_sha256": core.sha256_path(
            adjudication_execution.DEFAULT_ADJUDICATION_INPUT_SCHEMA_PATH
        ),
        "response_schema_sha256": core.sha256_path(
            adjudication_execution.DEFAULT_ADJUDICATION_RESPONSE_SCHEMA_PATH
        ),
        "run_record_schema_sha256": core.sha256_path(
            adjudication_execution.DEFAULT_ADJUDICATOR_RUN_RECORD_SCHEMA_PATH
        ),
        "reviewer_input_schema_sha256": core.sha256_path(core.DEFAULT_INPUT_SCHEMA_PATH),
        "reviewer_response_schema_sha256": core.sha256_path(
            core.DEFAULT_RESPONSE_SCHEMA_PATH
        ),
        "model_settings": MODEL_SETTINGS,
        "max_retries": 0,
        "semantic_truth_deterministically_established": False,
    }
    paths["run_manifest"].write_text(
        json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8"
    )
    core.write_jsonl(paths["records"], records)
    return paths


def _run_audit(paths: dict[str, Path]) -> dict[str, Any]:
    return audit_adjudicator_run(
        preparation_manifest_path=paths["preparation"],
        adjudication_payloads_path=paths["payloads"],
        run_manifest_path=paths["run_manifest"],
        adjudication_records_path=paths["records"],
        output_path=paths["audit"],
    )


def _base_payloads(tmp_path: Path, count: int) -> list[dict[str, Any]]:
    prepared = fixtures.prepare_smoke_downstream(tmp_path / "smoke")
    base = core.read_jsonl(prepared / "opus_adjudication_payloads.jsonl")[0]
    return [_clone_payload(base, index) for index in range(count)]


def test_audit_full_673_group_partition_and_retry_histories(tmp_path: Path) -> None:
    payloads = _base_payloads(tmp_path, 673)
    records = [
        _record(payload, "completed", response=_response(payload))
        for payload in payloads[:669]
    ]
    records.extend(
        [
            _record(payloads[669], "blocked", signal="SAFETY"),
            _record(payloads[669], "blocked", signal="SAFETY"),
            _record(payloads[670], "refused", signal="refusal"),
            _record(payloads[670], "blocked", signal="PROHIBITED_CONTENT"),
            _record(payloads[671], "blocked", signal="RECITATION"),
        ]
    )
    audit = _run_audit(_write_audit_inputs(tmp_path / "audit", payloads, records))

    assert audit["selected_group_count"] == 673
    assert audit["unique_completed_group_count"] == 669
    assert audit["terminal_provider_block_group_ids"] == [
        payloads[669]["generation_group_id"],
        payloads[670]["generation_group_id"],
    ]
    assert audit["nonterminal_unresolved_group_ids"] == [
        payloads[671]["generation_group_id"],
        payloads[672]["generation_group_id"],
    ]
    assert audit["external_api_calls"] == 0


def test_blocked_then_completed_is_completed_not_terminal(tmp_path: Path) -> None:
    payload = _base_payloads(tmp_path, 1)[0]
    records = [
        _record(payload, "blocked", signal="SAFETY"),
        _record(payload, "completed", response=_response(payload)),
    ]
    audit = _run_audit(_write_audit_inputs(tmp_path / "audit", [payload], records))
    assert audit["completed_group_ids"] == [payload["generation_group_id"]]
    assert audit["terminal_provider_block_group_ids"] == []
    assert audit["groups_with_blocked_histories_later_completed"] == [
        payload["generation_group_id"]
    ]


def test_usage_sums_attempts_once_without_top_level_double_count(tmp_path: Path) -> None:
    payloads = _base_payloads(tmp_path, 2)
    records = [
        _record(payloads[0], "provider_error", cost=0.10),
        _record(payloads[1], "completed", cost=0.20, response=_response(payloads[1])),
    ]
    audit = _run_audit(_write_audit_inputs(tmp_path / "audit", payloads, records))
    assert audit["usage"]["all_journal_records"]["cost"] == pytest.approx(0.30)
    assert audit["usage"]["all_journal_records"]["attempt_contribution_count"] == 2
    assert audit["usage"]["completed_journal_records"]["cost"] == pytest.approx(0.20)
    assert audit["invariants"]["usage_double_counting_detected"] is False


@pytest.mark.parametrize(
    "corruption, match",
    [
        ("duplicate_completed", "Multiple completed"),
        ("unknown_group", "unselected group"),
        ("bad_input_hash", "input hash differs"),
        ("invalid_completed", "Completed adjudication is invalid"),
    ],
)
def test_audit_fails_closed_on_journal_corruption(
    tmp_path: Path, corruption: str, match: str
) -> None:
    payload = _base_payloads(tmp_path, 1)[0]
    record = _record(payload, "completed", response=_response(payload))
    records = [record]
    if corruption == "duplicate_completed":
        records.append(deepcopy(record))
    elif corruption == "unknown_group":
        records[0]["generation_group_id"] = "airisk_generation_group_9999"
    elif corruption == "bad_input_hash":
        records[0]["input_payload_sha256"] = "0" * 64
    else:
        records[0]["parsed_structured_response"]["generation_group_id"] = (
            "airisk_generation_group_9999"
        )
    paths = _write_audit_inputs(tmp_path / "audit", [payload], records)
    with pytest.raises(SemanticReviewError, match=match):
        _run_audit(paths)


def test_terminal_and_nonterminal_resolution_routes_are_distinct_and_bound(
    tmp_path: Path,
) -> None:
    prepared = fixtures.prepare_smoke_downstream(tmp_path / "prepared")
    payloads = core.read_jsonl(prepared / "opus_adjudication_payloads.jsonl")
    comparisons = {
        item["generation_group_id"]: item
        for item in core.read_jsonl(prepared / "criterion_level_comparison.jsonl")
    }
    completed, terminal, nonterminal = payloads
    records = [
        _record(
            completed,
            "completed",
            response=fixtures.opus_response_for(
                completed,
                comparisons[completed["generation_group_id"]],
                disposition="candidate",
            ),
        ),
        _record(terminal, "blocked", signal="SAFETY"),
        _record(terminal, "blocked", signal="SAFETY"),
        _record(nonterminal, "blocked", signal="RECITATION"),
    ]
    paths = _write_audit_inputs(tmp_path / "audit", payloads, records)
    # Use the frozen preparation manifest rather than the minimal audit fixture one.
    paths["preparation"] = prepared / "opus_adjudication_manifest.json"
    _run_audit(paths)
    result = merge_resolved_source_reviews(
        comparison_path=prepared / "criterion_level_comparison.jsonl",
        sampling_manifest_path=paths["preparation"],
        private_provenance_path=prepared / "opus_adjudication_private_provenance.json",
        opus_payloads_path=paths["payloads"],
        blinded_source_payloads_path=prepared / "source_payloads.jsonl",
        adjudication_records_path=paths["records"],
        adjudication_run_manifest_path=paths["run_manifest"],
        adjudication_audit_path=paths["audit"],
        output_dir=tmp_path / "resolved",
    )
    resolved = {
        item["generation_group_id"]: item
        for item in core.read_jsonl(Path(result["resolved_source_reviews_path"]))
    }
    eligibility = {
        item["generation_group_id"]: item
        for item in core.read_jsonl(Path(result["source_eligibility_path"]))
    }
    terminal_record = resolved[terminal["generation_group_id"]]
    assert terminal_record["resolution_status"] == "pending_human_review"
    assert terminal_record["resolved_disposition"] == "unresolved"
    assert terminal_record["resolution_source"] == "pending"
    assert terminal_record["adjudication_evidence"] is None
    assert terminal_record["qc_evidence"] is None
    assert eligibility[terminal["generation_group_id"]]["eligibility"] == (
        "pending_human_review"
    )
    assert resolved[nonterminal["generation_group_id"]]["resolution_status"] == (
        "pending_adjudication"
    )
    assert eligibility[nonterminal["generation_group_id"]]["eligibility"] == (
        "pending_adjudication"
    )

    human = prepare_source_human_review_bundle(
        source_payloads_path=prepared / "source_payloads.jsonl",
        resolved_reviews_path=Path(result["resolved_source_reviews_path"]),
        eligibility_path=Path(result["source_eligibility_path"]),
        comparisons_path=prepared / "criterion_level_comparison.jsonl",
        source_resolution_manifest_path=Path(result["output_paths"]["manifest"])
        if "output_paths" in result
        else tmp_path / "resolved" / "source_resolution_manifest.json",
        output_dir=tmp_path / "human",
    )
    human_records = core.read_jsonl(Path(human["output_paths"]["queue_jsonl"]))
    queued = next(
        item
        for item in human_records
        if item["generation_group_id"] == terminal["generation_group_id"]
    )
    assert "adjudication_terminal_provider_block" in queued["queue_reason_codes"]

    tampered = json.loads(paths["audit"].read_text(encoding="utf-8"))
    tampered["artifacts"]["adjudication_journal"]["sha256"] = "0" * 64
    bad_audit = tmp_path / "bad-audit.json"
    bad_audit.write_text(json.dumps(tampered) + "\n", encoding="utf-8")
    with pytest.raises(SemanticReviewError, match="journal hash differs"):
        merge_resolved_source_reviews(
            comparison_path=prepared / "criterion_level_comparison.jsonl",
            sampling_manifest_path=paths["preparation"],
            private_provenance_path=prepared / "opus_adjudication_private_provenance.json",
            opus_payloads_path=paths["payloads"],
            blinded_source_payloads_path=prepared / "source_payloads.jsonl",
            adjudication_records_path=paths["records"],
            adjudication_run_manifest_path=paths["run_manifest"],
            adjudication_audit_path=bad_audit,
            output_dir=tmp_path / "must-not-write",
        )


def test_audit_uses_no_network_or_external_runtime(tmp_path: Path, monkeypatch) -> None:
    payload = _base_payloads(tmp_path, 1)[0]
    paths = _write_audit_inputs(
        tmp_path / "audit", [payload],
        [_record(payload, "completed", response=_response(payload))],
    )

    def fail_network(*_args, **_kwargs):
        raise AssertionError("offline audit attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    audit = _run_audit(paths)
    assert audit["external_api_calls"] == 0
