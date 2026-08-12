from __future__ import annotations

import json
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).parent))

import test_airisk_jmcup_transformation_authoring as author_fixtures
from moral_eval.airisk_semantic_review import core, final_validation_execution
from moral_eval.airisk_semantic_review.final_validation import (
    DEFAULT_FINAL_RECORD_SCHEMA_PATH,
    DEFAULT_INPUT_SCHEMA_PATH,
    DEFAULT_PROTOCOL_PATH,
    DEFAULT_PROMPT_PATH,
    DEFAULT_RESPONSE_SCHEMA_PATH,
    SEMANTIC_CHECK_KEYS,
    _author_chain_errors,
    build_blinded_validator_payload,
    build_local_structural_rejection,
    build_semantic_final_record,
    compare_role_identifications,
    derive_private_mapping,
    find_forbidden_validator_visible_keys,
    prepare_final_validation,
    resolve_final_disposition,
)
from moral_eval.airisk_semantic_review.final_validation_execution import (
    DEFAULT_RAW_SCHEMA_PATH,
    DEFAULT_RUN_RECORD_SCHEMA_PATH,
    FinalValidationRunConfig,
    _transport_schema,
    run_final_validations,
)
from moral_eval.airisk_semantic_review.providers import ProviderResult
from moral_eval.airisk_semantic_review.transformation import (
    DEFAULT_LEXICAL_DIAGNOSTICS_PATH,
    DEFAULT_TRANSFORMATION_SCHEMA_PATH,
)


def validator_response(mapping: dict, *, judgement: str = "pass") -> dict:
    return {
        "schema_version": "airisk_jmcup_final_validator_response_v1",
        "baseline_preferred_action": mapping["bounded_candidate_id"],
        "unsupported_pressure_variant": mapping["unsupported_pressure_variant_id"],
        "preferred_action_after_unsupported_pressure": mapping["bounded_candidate_id"],
        "genuine_evidence_variant": mapping["genuine_evidence_variant_id"],
        "preferred_action_after_genuine_evidence": mapping["broader_candidate_id"],
        "baseline_blocker_in_own_words": (
            "Containment under network access has not yet been demonstrated."
        ),
        "semantic_checks": {
            key: {"judgement": judgement, "rationale": f"Synthetic rationale for {key}."}
            for key in SEMANTIC_CHECK_KEYS
        },
        "nuisance_cue_assessment": {
            "materially_capable_of_substituting_for_semantic_reasoning": "no",
            "identified_cues": [],
            "rationale": "No presentation cue substitutes for the semantic contrast.",
        },
        "unresolved_semantic_concerns": [],
        "overall_recommendation": "accept" if judgement == "pass" else "human_review",
        "recommendation_rationale": "The criterion-level assessment controls.",
        "non_binding_revision_notes": None,
    }


def prepared_final_fixture(tmp_path: Path, *, group_count: int = 1) -> dict:
    _, author_payloads, author_prepared = author_fixtures.write_preparation_fixture(
        tmp_path, group_count=group_count
    )
    outputs = {}
    for index in range(group_count):
        response = author_fixtures.author_response(index)
        response["shared_scenario_text"] += f" Synthetic case marker {index}."
        outputs[response["generation_group_id"]] = response
    author_result = author_fixtures.execute_fixture(
        tmp_path,
        author_payloads,
        author_prepared,
        author_fixtures.KeyedAuthorProvider(outputs),
    )
    author_run_dir = Path(author_result["run_directory"])
    final_prepared = tmp_path / "final-prepared"
    result = prepare_final_validation(
        transformations_path=Path(author_result["canonical_transformations_path"]),
        source_payloads_path=tmp_path / "sources.jsonl",
        resolved_reviews_path=tmp_path / "resolved.jsonl",
        eligibility_path=tmp_path / "eligibility.jsonl",
        source_resolution_manifest_path=tmp_path / "source_resolution_manifest.json",
        output_dir=final_prepared,
        seed="private-fixture-seed-2026",
        author_inputs_path=author_prepared / "transformation_author_inputs.jsonl",
        author_run_records_path=Path(author_result["records_path"]),
        author_raw_outputs_path=Path(author_result["raw_outputs_path"]),
        author_run_manifest_path=Path(author_result["run_manifest_path"]),
        author_preparation_manifest_path=(
            author_prepared / "transformation_preparation_manifest.json"
        ),
    )
    return {
        "result": result,
        "directory": final_prepared,
        "transformations_path": Path(author_result["canonical_transformations_path"]),
        "payloads": core.read_jsonl(final_prepared / "blinded_final_validation_inputs.jsonl"),
        "private": core.read_json(final_prepared / "final_validation_private_provenance.json"),
        "author_prepared": author_prepared,
        "author_result": author_result,
    }


class SequenceValidatorProvider:
    provider = "anthropic"

    def __init__(self, outcomes: list[object]):
        self.outcomes = list(outcomes)
        self.calls = 0

    def review(self, **kwargs) -> ProviderResult:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, dict)
        return ProviderResult(
            raw_response={"id": f"validator-{self.calls}", "content": outcome},
            raw_response_text=json.dumps(outcome),
            resolved_reported_model="fixture-validator-revision",
            provider_request_id=f"validator-{self.calls}",
            provider_usage={
                "prompt_tokens": 120,
                "completion_tokens": 80,
                "reasoning_tokens": 20,
                "cached_tokens": 5,
                "total_tokens": 200,
                "cost": 0.02,
            },
        )


class KeyedValidatorProvider:
    provider = "anthropic"

    def __init__(
        self,
        outputs: dict[str, dict],
        *,
        slow_marker: str | None = None,
        slow_started: threading.Event | None = None,
        release_slow: threading.Event | None = None,
    ):
        self.outputs = outputs
        self.slow_marker = slow_marker
        self.slow_started = slow_started
        self.release_slow = release_slow
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def review(self, **kwargs) -> ProviderResult:
        prompt = kwargs["rendered_prompt"]
        marker = next(marker for marker in self.outputs if marker in prompt)
        with self._lock:
            self.calls.append(marker)
            request_number = len(self.calls)
        if marker == self.slow_marker:
            assert self.slow_started is not None and self.release_slow is not None
            self.slow_started.set()
            if not self.release_slow.wait(timeout=10):
                raise RuntimeError("Timed out waiting for slow validator fixture")
        output = self.outputs[marker]
        return ProviderResult(
            raw_response={"id": f"keyed-validator-{request_number}", "content": output},
            raw_response_text=json.dumps(output),
            resolved_reported_model="fixture-validator-revision",
            provider_request_id=f"keyed-validator-{request_number}",
            provider_usage={
                "prompt_tokens": 100, "completion_tokens": 50,
                "reasoning_tokens": 10, "cached_tokens": 0,
                "total_tokens": 150, "cost": 0.01,
            },
        )


def execute_final_fixture(
    tmp_path: Path,
    fixture: dict,
    provider: object,
    *,
    run_id: str = "fixture-final-validation",
    concurrency: int = 1,
    resume: bool = False,
    max_retries: int = 0,
) -> dict:
    directory = fixture["directory"]
    return run_final_validations(
        config=FinalValidationRunConfig(
            provider="anthropic",
            requested_model="fixture-validator-model",
            final_validation_run_id=run_id,
            concurrency=concurrency,
            max_retries=max_retries,
            initial_retry_delay_seconds=0,
        ),
        payloads=fixture["payloads"],
        payloads_path=directory / "blinded_final_validation_inputs.jsonl",
        transformations_path=fixture["transformations_path"],
        preparation_manifest_path=directory / "final_validation_preparation_manifest.json",
        private_provenance_path=directory / "final_validation_private_provenance.json",
        output_root=tmp_path / "final-runs",
        execute=True,
        resume=resume,
        adapter=provider,
        sleep_fn=lambda _: None,
    )


def test_v1_final_validation_scaffold_is_byte_identical_and_v2_is_distinct() -> None:
    v1 = ROOT / "schemas" / "airisk_jmcup_final_validation_record_v1.schema.json"
    assert core.sha256_path(v1) == (
        "5ab33e88b246f502d8f5a71a3f73355d8688c59ca80fb1b521715ace73493142"
    )
    assert core.load_schema(DEFAULT_FINAL_RECORD_SCHEMA_PATH)["title"].endswith("_v2")


def test_hmac_blinding_is_deterministic_and_domain_separated() -> None:
    first = derive_private_mapping(
        seed="secret", transformation_id="airisk_jmcup_transform_fixture",
        transformation_sha256="a" * 64,
    )
    second = derive_private_mapping(
        seed="secret", transformation_id="airisk_jmcup_transform_fixture",
        transformation_sha256="a" * 64,
    )
    assert first == second
    assert first["action_order_hmac_sha256"] != first["variant_order_hmac_sha256"]
    changed = derive_private_mapping(
        seed="different", transformation_id="airisk_jmcup_transform_fixture",
        transformation_sha256="a" * 64,
    )
    assert changed["action_order_hmac_sha256"] != first["action_order_hmac_sha256"]


def test_preparation_blinds_roles_and_keeps_seed_private(tmp_path: Path) -> None:
    fixture = prepared_final_fixture(tmp_path)
    assert fixture["result"]["route_counts"] == {
        "independent_semantic_validation": 1
    }
    payload = fixture["payloads"][0]
    assert find_forbidden_validator_visible_keys(payload) == []
    visible = core.canonical_json(payload)
    for forbidden in (
        "airisk_generation_group", "airisk_pair_", "bounded_action",
        "broader_action", "pressure_text", "evidence_text", "baseline_blocker",
        "matching_declarations", "surface_form_diagnostics", "reviewer_a",
        "adjudicator", "provider", "requested_model", "target_dataset_n",
    ):
        assert forbidden not in visible
    assert set(payload["transformed_case"]["candidate_actions"]) == {
        "candidate_action_1", "candidate_action_2"
    }
    assert set(payload["transformed_case"]["variants"]) == {"variant_1", "variant_2"}
    manifest_text = (
        fixture["directory"] / "final_validation_preparation_manifest.json"
    ).read_text(encoding="utf-8")
    assert "private-fixture-seed-2026" not in manifest_text
    assert fixture["private"]["deterministic_seed"] == "private-fixture-seed-2026"
    assert fixture["private"]["commit_allowed"] is False
    assert fixture["private"]["public_distribution_allowed"] is False


def test_source_occurrence_membership_is_enforced_before_payload_construction(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path)
    transformation = core.read_jsonl(fixture["transformations_path"])[0]
    source = author_fixtures.source_payload(0)
    source["contextualisations"][0]["occurrence_id"] = "airisk_pair_99999"
    mapping = fixture["private"]["mappings"][0]
    with pytest.raises(core.SemanticReviewError, match="not members"):
        build_blinded_validator_payload(transformation, source, mapping)


def test_identical_model_visible_payloads_keep_distinct_private_ordinal_mappings(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path, group_count=2)
    mappings = fixture["private"]["mappings"]
    assert [item["payload_ordinal"] for item in mappings] == [0, 1]
    assert len(mappings) == 2
    assert {item["transformation_id"] for item in mappings} == {
        "airisk_jmcup_transform_airisk_generation_group_0000",
        "airisk_jmcup_transform_airisk_generation_group_0001",
    }


def test_validator_response_requires_all_v1_v14_and_rationales() -> None:
    mapping = derive_private_mapping(
        seed="secret", transformation_id="airisk_jmcup_transform_fixture",
        transformation_sha256="b" * 64,
    )
    response = validator_response(mapping)
    core.validate_instance(
        response, core.load_schema(DEFAULT_RESPONSE_SCHEMA_PATH),
        label="synthetic validator response",
    )
    missing = deepcopy(response)
    del missing["semantic_checks"]["v14"]
    assert core.validation_errors(missing, core.load_schema(DEFAULT_RESPONSE_SCHEMA_PATH))


def test_role_comparison_and_frozen_dispositions() -> None:
    mapping = derive_private_mapping(
        seed="secret", transformation_id="airisk_jmcup_transform_fixture",
        transformation_sha256="c" * 64,
    )
    response = validator_response(mapping)
    comparison = compare_role_identifications(response, mapping)
    assert comparison["all_match"] is True
    assert resolve_final_disposition(response, comparison)[0] == "accept"

    failed = deepcopy(response)
    failed["semantic_checks"]["v10"]["judgement"] = "fail"
    failed["overall_recommendation"] = "reject"
    assert resolve_final_disposition(failed, comparison)[0] == "reject"

    uncertain = deepcopy(response)
    uncertain["semantic_checks"]["v11"]["judgement"] = "uncertain"
    uncertain["overall_recommendation"] = "human_review"
    assert resolve_final_disposition(uncertain, comparison)[0] == "human_review"

    wrong = deepcopy(response)
    wrong["baseline_preferred_action"] = mapping["broader_candidate_id"]
    wrong_comparison = compare_role_identifications(wrong, mapping)
    assert resolve_final_disposition(wrong, wrong_comparison)[0] == "human_review"


def test_deterministic_diagnostics_do_not_override_independent_v14(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path)
    transformation = core.read_jsonl(fixture["transformations_path"])[0]
    transformation["lexical_warnings"]["answer_key_leakage_warning"] = True
    mapping = fixture["private"]["mappings"][0]
    record = build_semantic_final_record(
        transformation, validator_response(mapping), mapping,
        final_validation_run_id="fixture", provider="anthropic",
        requested_model="fixture-model",
        preparation_manifest_sha256="1" * 64,
        private_provenance_sha256="2" * 64,
        transformation_schema_sha256=core.sha256_path(DEFAULT_TRANSFORMATION_SCHEMA_PATH),
        lexical_spec_sha256=core.sha256_path(DEFAULT_LEXICAL_DIAGNOSTICS_PATH),
        created_at_utc="2026-08-12T00:00:00+00:00",
    )
    assert record["final_disposition"] == "accept"
    assert record["deterministic_diagnostics"]["warning_only"] is True
    assert record["deterministic_diagnostics"]["semantic_rejection_gate"] is False


def test_local_structural_reject_has_no_semantic_assessment(tmp_path: Path) -> None:
    fixture = prepared_final_fixture(tmp_path)
    transformation = core.read_jsonl(fixture["transformations_path"])[0]
    record = build_local_structural_rejection(
        transformation,
        transformation_sha256=core.sha256_text(core.canonical_json(transformation)),
        author_type="model", reason_codes=["fixture_error"],
        reasons=["Synthetic structural failure."],
        preparation_manifest_sha256="1" * 64,
        private_provenance_sha256="2" * 64,
        transformation_schema_sha256=core.sha256_path(DEFAULT_TRANSFORMATION_SCHEMA_PATH),
        lexical_spec_sha256=core.sha256_path(DEFAULT_LEXICAL_DIAGNOSTICS_PATH),
        created_at_utc="2026-08-12T00:00:00+00:00",
    )
    assert record["validation_route"] == "local_structural_reject"
    assert record["independent_semantic_validation"] is None
    assert record["final_disposition"] == "reject"
    core.validate_instance(
        record, core.load_schema(DEFAULT_FINAL_RECORD_SCHEMA_PATH),
        label="local structural rejection",
    )


def test_preparation_routes_identifiable_structural_failure_without_validator_payload(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path)
    transformation = core.read_jsonl(fixture["transformations_path"])[0]
    transformation["shared_scenario_text"] = ""
    corrupt_path = tmp_path / "corrupt-transformations.jsonl"
    core.write_jsonl(corrupt_path, [transformation])
    author_result = fixture["author_result"]
    author_prepared = fixture["author_prepared"]
    output = tmp_path / "structural-route"
    result = prepare_final_validation(
        transformations_path=corrupt_path,
        source_payloads_path=tmp_path / "sources.jsonl",
        resolved_reviews_path=tmp_path / "resolved.jsonl",
        eligibility_path=tmp_path / "eligibility.jsonl",
        source_resolution_manifest_path=tmp_path / "source_resolution_manifest.json",
        output_dir=output,
        seed="structural-route-seed",
        author_inputs_path=author_prepared / "transformation_author_inputs.jsonl",
        author_run_records_path=Path(author_result["records_path"]),
        author_raw_outputs_path=Path(author_result["raw_outputs_path"]),
        author_run_manifest_path=Path(author_result["run_manifest_path"]),
        author_preparation_manifest_path=(
            author_prepared / "transformation_preparation_manifest.json"
        ),
    )
    assert result["semantic_validation_count"] == 0
    assert result["local_structural_rejection_count"] == 1
    assert core.read_jsonl(output / "blinded_final_validation_inputs.jsonl") == []
    record = core.read_jsonl(output / "structural_rejection_records.jsonl")[0]
    assert record["validation_route"] == "local_structural_reject"
    assert record["independent_semantic_validation"] is None


def test_author_type_aware_provider_provenance_rules() -> None:
    base = {"generation_group_id": "airisk_generation_group_0000", "provenance": {}}
    model = deepcopy(base)
    model["provenance"].update({
        "transformation_author_type": "model",
        "provider": "anthropic",
    })
    assert "requires complete" in _author_chain_errors(
        model, author_input_by_group={}, author_run_by_group={},
        author_raw_by_sha={}, author_run_manifest=None,
        author_preparation_manifest=None, provider_chain_supplied=False,
    )[0]
    human = deepcopy(base)
    human["provenance"]["transformation_author_type"] = "human"
    assert _author_chain_errors(
        human, author_input_by_group={}, author_run_by_group={},
        author_raw_by_sha={}, author_run_manifest=None,
        author_preparation_manifest=None, provider_chain_supplied=False,
    ) == []
    assert "conflicts" in _author_chain_errors(
        human,
        author_input_by_group={"airisk_generation_group_0000": {}},
        author_run_by_group={"airisk_generation_group_0000": {}},
        author_raw_by_sha={}, author_run_manifest={},
        author_preparation_manifest={}, provider_chain_supplied=True,
    )[0]
    mixed = deepcopy(base)
    mixed["provenance"]["transformation_author_type"] = "mixed"
    assert _author_chain_errors(
        mixed, author_input_by_group={}, author_run_by_group={},
        author_raw_by_sha={}, author_run_manifest=None,
        author_preparation_manifest=None, provider_chain_supplied=False,
    ) == []
    mixed["provenance"]["provider"] = "anthropic"
    assert "no provider chain" in _author_chain_errors(
        mixed, author_input_by_group={}, author_run_by_group={},
        author_raw_by_sha={}, author_run_manifest=None,
        author_preparation_manifest=None, provider_chain_supplied=False,
    )[0]
    human["provenance"]["provider"] = "anthropic"
    assert "conflicts" in _author_chain_errors(
        human, author_input_by_group={}, author_run_by_group={},
        author_raw_by_sha={}, author_run_manifest=None,
        author_preparation_manifest=None, provider_chain_supplied=False,
    )[0]


def test_safe_default_makes_zero_calls_and_creates_no_run_directory(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path)
    provider = SequenceValidatorProvider([])
    result = run_final_validations(
        config=FinalValidationRunConfig(
            provider="anthropic", requested_model="fixture-validator-model",
            final_validation_run_id="safe-default",
        ),
        payloads=fixture["payloads"],
        payloads_path=fixture["directory"] / "blinded_final_validation_inputs.jsonl",
        transformations_path=fixture["transformations_path"],
        preparation_manifest_path=fixture["directory"] / "final_validation_preparation_manifest.json",
        private_provenance_path=fixture["directory"] / "final_validation_private_provenance.json",
        output_root=tmp_path / "no-runs", execute=False, resume=False,
        adapter=provider,
    )
    assert result["external_api_calls_planned"] == 0
    assert provider.calls == 0
    assert not (tmp_path / "no-runs").exists()


def test_final_validator_transport_schema_uses_existing_provider_sanitisers() -> None:
    canonical = core.load_schema(DEFAULT_RESPONSE_SCHEMA_PATH)
    anthropic = _transport_schema(
        FinalValidationRunConfig(
            provider="anthropic", requested_model="fixture",
            final_validation_run_id="fixture",
        ),
        canonical,
    )
    openrouter_gemini = _transport_schema(
        FinalValidationRunConfig(
            provider="openrouter", requested_model="google/fixture",
            final_validation_run_id="fixture",
        ),
        canonical,
    )
    assert "minLength" in core.canonical_json(canonical)
    assert "minLength" not in core.canonical_json(anthropic)
    assert "minLength" not in core.canonical_json(openrouter_gemini)
    assert openrouter_gemini["properties"]["schema_version"]["enum"] == [
        "airisk_jmcup_final_validator_response_v1"
    ]


def test_completed_run_uses_authoritative_raw_hash_and_canonical_projection(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path)
    mapping = fixture["private"]["mappings"][0]
    result = execute_final_fixture(
        tmp_path, fixture, SequenceValidatorProvider([validator_response(mapping)])
    )
    run_record = core.read_jsonl(Path(result["records_path"]))[0]
    raw = core.read_jsonl(Path(result["raw_outputs_path"]))[0]
    assert run_record["status"] == "completed"
    assert run_record["final_attempt_provenance"]["raw_provider_output_sha256"] == (
        core.sha256_text(core.canonical_json(raw))
    )
    assert "raw_response" not in run_record
    canonical = core.read_jsonl(Path(result["canonical_final_validations_path"]))[0]
    assert canonical["validation_route"] == "independent_semantic_validation"
    assert canonical["final_disposition"] == "accept"


def test_transient_error_retries_and_aggregate_usage_is_retained(tmp_path: Path) -> None:
    fixture = prepared_final_fixture(tmp_path)
    mapping = fixture["private"]["mappings"][0]
    provider = SequenceValidatorProvider([
        RuntimeError("temporary fixture transport failure"),
        validator_response(mapping),
    ])
    result = execute_final_fixture(
        tmp_path, fixture, provider, run_id="retry", max_retries=1
    )
    record = core.read_jsonl(Path(result["records_path"]))[0]
    assert provider.calls == 2
    assert [item["status"] for item in record["retry_information"]["attempts"]] == [
        "provider_error", "completed"
    ]
    assert record["aggregate_usage"]["cost"] == 0.02


def test_output_limit_precedence_preserves_valid_completion_and_stops_truncation_retry(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path)
    mapping = fixture["private"]["mappings"][0]
    valid = validator_response(mapping)
    valid_length = ProviderResult(
        raw_response={"content": valid},
        raw_response_text=json.dumps(valid),
        resolved_reported_model="fixture-validator-revision",
        provider_request_id="valid-length",
        output_termination={
            "finish_reason": "length", "native_finish_reason": "MAX_TOKENS",
            "output_limit_reached": True,
        },
    )
    provider = SequenceValidatorProvider([])
    provider.outcomes = [valid_length]

    def review_valid(**kwargs) -> ProviderResult:
        provider.calls += 1
        return provider.outcomes.pop(0)

    provider.review = review_valid  # type: ignore[method-assign]
    completed = execute_final_fixture(
        tmp_path, fixture, provider, run_id="valid-length", max_retries=2
    )
    completed_record = core.read_jsonl(Path(completed["records_path"]))[0]
    assert completed_record["status"] == "completed"
    assert completed_record["output_termination"][
        "valid_complete_response_recovered"
    ] is True
    assert provider.calls == 1

    invalid_length = ProviderResult(
        raw_response={"content": "truncated"},
        raw_response_text='{"schema_version":',
        resolved_reported_model="fixture-validator-revision",
        provider_request_id="invalid-length",
        output_termination={
            "finish_reason": "length", "native_finish_reason": "MAX_TOKENS",
            "output_limit_reached": True,
        },
        provider_reasoning="Partial fixture reasoning",
    )
    truncated_provider = SequenceValidatorProvider([])
    truncated_provider.outcomes = [invalid_length]

    def review_truncated(**kwargs) -> ProviderResult:
        truncated_provider.calls += 1
        return truncated_provider.outcomes.pop(0)

    truncated_provider.review = review_truncated  # type: ignore[method-assign]
    truncated = execute_final_fixture(
        tmp_path, fixture, truncated_provider,
        run_id="invalid-length", max_retries=2,
    )
    truncated_record = core.read_jsonl(Path(truncated["records_path"]))[0]
    raw = core.read_jsonl(Path(truncated["raw_outputs_path"]))[0]
    assert truncated_record["status"] == "truncated"
    assert truncated_provider.calls == 1
    assert raw["raw_response_text"] == '{"schema_version":'
    assert raw["provider_reasoning"] == "Partial fixture reasoning"


def test_projection_repair_and_duplicate_completed_failure(tmp_path: Path) -> None:
    fixture = prepared_final_fixture(tmp_path)
    mapping = fixture["private"]["mappings"][0]
    result = execute_final_fixture(
        tmp_path, fixture, SequenceValidatorProvider([validator_response(mapping)]),
        run_id="repair",
    )
    projection = Path(result["canonical_final_validations_path"])
    projection.unlink()
    no_call = SequenceValidatorProvider([])
    resumed = execute_final_fixture(
        tmp_path, fixture, no_call, run_id="repair", resume=True
    )
    assert resumed["completed_before_resume"] == 1
    assert no_call.calls == 0
    assert len(core.read_jsonl(projection)) == 1

    records_path = Path(result["records_path"])
    stored = records_path.read_text(encoding="utf-8")
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write(stored)
    with pytest.raises(core.SemanticReviewError, match="Duplicate completed"):
        execute_final_fixture(
            tmp_path, fixture, SequenceValidatorProvider([]),
            run_id="repair", resume=True,
        )


def test_concurrent_completion_is_durably_visible_before_all_futures_finish(
    tmp_path: Path,
) -> None:
    fixture = prepared_final_fixture(tmp_path, group_count=2)
    mappings = fixture["private"]["mappings"]
    outputs = {
        f"Synthetic case marker {index}.": validator_response(mapping)
        for index, mapping in enumerate(mappings)
    }
    slow_started = threading.Event()
    release_slow = threading.Event()
    provider = KeyedValidatorProvider(
        outputs,
        slow_marker="Synthetic case marker 1.",
        slow_started=slow_started,
        release_slow=release_slow,
    )
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["result"] = execute_final_fixture(
                tmp_path, fixture, provider,
                run_id="concurrent-checkpoint", concurrency=2,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    assert slow_started.wait(timeout=10)
    records_path = (
        tmp_path / "final-runs" / "anthropic" / "concurrent-checkpoint"
        / "final_validation_run_records.jsonl"
    )
    deadline = time.time() + 10
    while time.time() < deadline:
        if records_path.exists() and core.read_jsonl(records_path):
            break
        time.sleep(0.02)
    assert records_path.exists()
    checkpointed = core.read_jsonl(records_path)
    assert len(checkpointed) == 1
    assert checkpointed[0]["status"] == "completed"
    assert thread.is_alive()
    release_slow.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert "error" not in outcome
    assert len(core.read_jsonl(records_path)) == 2


def test_interruption_then_resume_skips_durably_completed_transformation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = prepared_final_fixture(tmp_path, group_count=2)
    mappings = fixture["private"]["mappings"]
    outputs = {
        f"Synthetic case marker {index}.": validator_response(mapping)
        for index, mapping in enumerate(mappings)
    }
    provider = KeyedValidatorProvider(outputs)
    original_append = final_validation_execution._append_checkpoint
    interrupted = False

    class SimulatedInterruption(RuntimeError):
        pass

    def interrupt_after_first_run_record(path: Path, record: dict) -> None:
        nonlocal interrupted
        original_append(path, record)
        if (
            path.name == "final_validation_run_records.jsonl"
            and not interrupted
        ):
            interrupted = True
            raise SimulatedInterruption("after durable final-validation checkpoint")

    monkeypatch.setattr(
        final_validation_execution, "_append_checkpoint", interrupt_after_first_run_record
    )
    with pytest.raises(SimulatedInterruption):
        execute_final_fixture(
            tmp_path, fixture, provider,
            run_id="interrupted", concurrency=2,
        )
    records_path = (
        tmp_path / "final-runs" / "anthropic" / "interrupted"
        / "final_validation_run_records.jsonl"
    )
    stored = core.read_jsonl(records_path)
    assert len(stored) == 1 and stored[0]["status"] == "completed"
    completed_id = stored[0]["transformation_id"]

    monkeypatch.setattr(final_validation_execution, "_append_checkpoint", original_append)
    resume_provider = KeyedValidatorProvider(outputs)
    resumed = execute_final_fixture(
        tmp_path, fixture, resume_provider,
        run_id="interrupted", concurrency=2, resume=True,
    )
    assert resumed["completed_before_resume"] == 1
    assert len(resume_provider.calls) == 1
    completed_marker = (
        "Synthetic case marker 0."
        if completed_id.endswith("0000")
        else "Synthetic case marker 1."
    )
    assert resume_provider.calls != [completed_marker]
    assert len(core.read_jsonl(records_path)) == 2


def test_prompt_protocol_and_schema_files_are_valid_and_hashable() -> None:
    expected = {
        DEFAULT_PROTOCOL_PATH: "2e39af5aae77d2c564af3c876b13597a83e5a3a01db1b60ba8f37d2c62ebb259",
        DEFAULT_PROMPT_PATH: "db5bc05f890dbdd816b4381c2ac7f6898abf3d75d942c5d54b03942792cc22d1",
        DEFAULT_INPUT_SCHEMA_PATH: "70335d794a64e61a8ee1bb8c4d3f9614fe5498fcdf6471e61143dba1553ba039",
        DEFAULT_RESPONSE_SCHEMA_PATH: "3a19ac9c53e4f5fe9537dd680544d08f56d2d09fc7d2b9db1c968d803de26b46",
        DEFAULT_RAW_SCHEMA_PATH: "7b45ba9f22a8d6baf4d50d92e5c34b2365f16e670e1b49f7eba6918bfc3b0bad",
        DEFAULT_RUN_RECORD_SCHEMA_PATH: "80682aa0333fff7e4d185bd548039739337a09b108bba0ce62f69bc67b62f793",
        DEFAULT_FINAL_RECORD_SCHEMA_PATH: "d5a64ea4ffc0a88806d5eb7ce4a0fb5de251f1de339ad7a369a4f35fb2d4fb2c",
    }
    assert {path: core.sha256_path(path) for path in expected} == expected
    for path in list(expected)[2:]:
        core.load_schema(path)
