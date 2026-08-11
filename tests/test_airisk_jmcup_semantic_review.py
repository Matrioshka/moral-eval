from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.airisk_semantic_review import core
from moral_eval.airisk_semantic_review.adjudication import (
    classify_pair,
    compare_review_pair,
    prepare_adjudication,
)
from moral_eval.airisk_semantic_review.execution import ReviewRunConfig, run_reviews
from moral_eval.airisk_semantic_review.providers import (
    AnthropicReviewerAdapter,
    GeminiReviewerAdapter,
    OpenRouterReviewerAdapter,
    ProviderResult,
    anthropic_transport_schema,
    gemini_transport_schema,
)
from scripts.log_prob.run_airisk_jmcup_semantic_review import build_parser


def action(row_index: int, number: int) -> dict:
    return {
        "source_row_index": row_index,
        "action": f"Action {number}: Exact source action {number}",
        "action_body": f"Exact source action {number}",
        "values": [f"value {number}"],
        "risky_behaviors": ["Deception"] if number == 1 else [],
        "context": "Technology",
        "targets": ["Human"] if number == 1 else None,
        "targets_available": number == 1,
        "targets_source_configuration": "model_eval" if number == 1 else None,
        "targets_source_row_index": row_index if number == 1 else None,
        "targets_join_method": "exact" if number == 1 else None,
        "targets_source_dilemma_sha256": "a" * 64 if number == 1 else None,
        "heuristic_priority_score": 999,
    }


def contextualisation(index: int) -> dict:
    return {
        "group_occurrence_ordinal": index,
        "occurrence_id": f"airisk_pair_{index:05d}",
        "pair_index": index,
        "dilemma_id": f"airisk_dilemma_{index:016x}",
        "dilemma_sha256": f"{index + 1:064x}",
        "dilemma": f"Source dilemma {index}",
        "actions": {
            "action_1": action(index * 2, 1),
            "action_2": action(index * 2 + 1, 2),
        },
        "risky_behaviors": ["Deception"],
        "contexts": ["Technology"],
        "semantic_family": "forbidden_family",
        "high_stakes_domain": "forbidden_domain",
        "blocker_family_hint": "forbidden_blocker",
        "heuristic_queue_priority_band": "higher_priority",
        "heuristic_jmcup_source_priority_score": 100,
    }


def queue_record(group_ordinal: int = 0, count: int = 2) -> dict:
    group_id = f"airisk_generation_group_{group_ordinal:04d}"
    return {
        "review_schema_version": "airisk_jmcup_group_review_v3",
        "generation_group_id": group_id,
        "generation_group_ordinal": group_ordinal,
        "generation_group_reconstruction": {
            "method": "airisk_contiguous_generation_group_v3",
            "confidence": "partially_inferred",
            "occurrence_count": count,
            "unique_dilemma_count": count,
            "pair_index_start": 0,
            "pair_index_end": count - 1,
            "primary_hypothesis_clearly_preferred": False,
        },
        "source_seed_match": {
            "status": "candidate_matches_only",
            "confidence": "unavailable",
            "provenance": "anthropics/evals@revision",
            "source_seed_record": None,
            "candidate_matches": [
                {
                    "question": "This deterministic source-match ranking must not leak",
                    "source_match_raw_score": 999,
                }
            ],
        },
        "metadata_summary": {"semantic_family_counts": {"forbidden": count}},
        "heuristic_group_queue_priority_band": "higher_priority",
        "heuristic_best_member_score": 999,
        "heuristic_group_score_median": 999,
        "heuristic_disclaimer": "forbidden",
        "representative_candidates": [
            {
                "rank": 1,
                "deterministic_ranking_reasons": ["forbidden"],
            }
        ],
        "pre_outcome_audit_sample": group_ordinal == 0,
        "pre_outcome_audit_sample_rank": 1 if group_ordinal == 0 else None,
        "all_contextualisations": [contextualisation(index) for index in range(count)],
        "reviewer_fields": {
            "h1_same_decision_axis": "yes",
            "source_fidelity": "high",
        },
        "previous_reviewer_response": {"verdict": "forbidden"},
    }


def response(
    group_id: str = "airisk_generation_group_0000",
    *,
    judgements: dict[str, str] | None = None,
    fidelity: str = "high",
    rewrite: str = "moderate",
    verdict: str = "strong_transform_candidate",
) -> dict:
    values = {key: "yes" for key in core.CRITERION_KEYS}
    values.update(judgements or {})
    return {
        "schema_version": core.RESPONSE_SCHEMA_VERSION,
        "generation_group_id": group_id,
        "selected_representative_occurrence_id": "airisk_pair_00000",
        "group_useful_without_clean_representative": False,
        "criteria": {
            key: {"judgement": judgement, "rationale": f"Rationale for {key}"}
            for key, judgement in values.items()
        },
        "proposed_bounded_action": "Use bounded supervised access",
        "proposed_broader_action": "Use broader controlled access",
        "proposed_baseline_blocker": "Validation is incomplete",
        "proposed_evidence": "Independent validation reports acceptable risk",
        "proposed_transformation_family": "controlled_access",
        "rewrite_level": rewrite,
        "construct_validity_concerns": [],
        "semantic_independence_concern": None,
        "likely_transformation_duplicate_of": None,
        "source_fidelity": fidelity,
        "reviewer_verdict": verdict,
        "reviewer_confidence": "high",
        "review_summary": "A concise independent review.",
    }


def review_record(
    run_id: str,
    parsed: dict,
    *,
    provider: str = "anthropic",
) -> dict:
    return {
        "record_schema_version": core.RUN_RECORD_SCHEMA_VERSION,
        "reviewer_run_id": run_id,
        "generation_group_id": parsed["generation_group_id"],
        "provider": provider,
        "requested_model": "mock-model",
        "resolved_reported_model": "mock-model-revision",
        "provider_request_id": "mock-request-id",
        "provider_generation_id": None,
        "actual_routed_provider": None,
        "provider_usage": None,
        "aggregate_usage": {
            "prompt_tokens": None,
            "completion_tokens": None,
            "reasoning_tokens": None,
            "cached_tokens": None,
            "total_tokens": None,
            "cost": None,
            "reported_attempt_counts": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "reasoning_tokens": 0,
                "cached_tokens": 0,
                "total_tokens": 0,
                "cost": 0,
            },
        },
        "output_termination": None,
        "provider_reasoning": None,
        "prompt_version": core.PROMPT_VERSION,
        "prompt_sha256": "1" * 64,
        "response_schema_sha256": "2" * 64,
        "input_payload_sha256": "3" * 64,
        "status": "completed",
        "raw_response": parsed,
        "raw_response_text": json.dumps(parsed),
        "parsed_structured_response": parsed,
        "provider_block_metadata": None,
        "timestamp_utc": "2026-08-11T00:00:00+00:00",
        "model_settings": {"temperature": 0.0, "max_output_tokens": 4096},
        "retry_information": {"max_retries": 0, "attempt_count": 1, "attempts": []},
        "validation_errors": [],
    }


class FakeProvider:
    provider = "anthropic"

    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)
        self.calls = []

    def review(self, **kwargs) -> ProviderResult:
        self.calls.append(kwargs)
        parsed = self.outputs.pop(0)
        return ProviderResult(
            raw_response={"model": "reported-model", "content": parsed},
            raw_response_text=json.dumps(parsed),
            resolved_reported_model="reported-model",
            provider_request_id=f"request-{len(self.calls)}",
        )


class FakeOpenRouterResponse:
    def __init__(
        self,
        body: object,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.text = json.dumps(body)

    def json(self) -> object:
        return deepcopy(self._body)


class FakeOpenRouterSession:
    def __init__(self, responses: list[object]):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs) -> FakeOpenRouterResponse:
        self.calls.append({"url": url, **kwargs})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, FakeOpenRouterResponse)
        return item


def openrouter_success(
    parsed: dict,
    *,
    model: str,
    upstream_provider: str,
    finish_reason: str = "stop",
    native_finish_reason: str | None = None,
    content: object = Ellipsis,
    reasoning: object = Ellipsis,
    reasoning_details: object = Ellipsis,
    usage: dict | None = None,
    request_id: str = "openrouter-request-1",
    generation_id: str = "openrouter-generation-1",
) -> FakeOpenRouterResponse:
    message = {
        "role": "assistant",
        "content": json.dumps(parsed) if content is Ellipsis else content,
    }
    if reasoning is not Ellipsis:
        message["reasoning"] = reasoning
    if reasoning_details is not Ellipsis:
        message["reasoning_details"] = reasoning_details
    choice = {
        "index": 0,
        "finish_reason": finish_reason,
        "message": message,
    }
    if native_finish_reason is not None:
        choice["native_finish_reason"] = native_finish_reason
    return FakeOpenRouterResponse(
        {
            "id": request_id,
            "model": model,
            "choices": [choice],
            "usage": usage
            or {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
            "openrouter_metadata": {
                "requested": model,
                "endpoints": {
                    "available": [
                        {
                            "model": model,
                            "provider": upstream_provider,
                            "selected": True,
                        }
                    ]
                },
            },
        },
        headers={"X-Generation-Id": generation_id},
    )


def write_queue(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(core.canonical_json(record) + "\n" for record in records),
        encoding="utf-8",
    )


def test_frozen_prompt_preserves_all_seven_hard_questions() -> None:
    prompt = core.load_prompt()
    required = [
        "same decision axis",
        "specific baseline safety blocker",
        "adding no relevant factual evidence",
        "remove that same blocker",
        "same broader action become reasonably well justified",
        "not intrinsically wrongful",
        "rather than a deeply contested first-order moral dilemma",
    ]
    assert all(fragment in prompt for fragment in required)
    assert "Do not weaken, substitute, average, or reinterpret any criterion" in prompt
    assert (
        "H2, H4 and H5 must refer to the same blocker and the same "
        "bounded-to-broader action transition" in prompt
    )
    assert "merely makes the broader action somewhat safer" in prompt
    assert "substantive findings or observations" in prompt
    assert '"all safety requirements have now been satisfied"' in prompt


def test_canonical_response_schema_bytes_and_sha_are_unchanged_by_transport() -> None:
    path = core.DEFAULT_RESPONSE_SCHEMA_PATH
    original_bytes = path.read_bytes()
    assert core.sha256_bytes(original_bytes) == (
        "006f0cf372b5d337708a1eb3be8b572ea4e87b29f9db4850dc188ecf575b1716"
    )
    canonical = core.load_schema(path)
    original_object = deepcopy(canonical)

    anthropic_transport_schema(canonical)
    gemini_transport_schema(canonical)

    assert canonical == original_object
    assert path.read_bytes() == original_bytes


def test_other_frozen_reviewer_inputs_retain_their_sha() -> None:
    assert core.sha256_path(core.DEFAULT_PROMPT_PATH) == (
        "80d898c68d7fef92155584f6fcea09f2264ec5bdc2f9162346b325f73a10480d"
    )
    assert core.sha256_path(core.DEFAULT_INPUT_SCHEMA_PATH) == (
        "dc311c92347f148ffc4f917728430a19948af478a0ac06905d6f8b3b38ce10c6"
    )


def _schema_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value)
        for child in value.values():
            keys.update(_schema_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_schema_keys(child))
    return keys


def test_provider_transport_schemas_remove_only_provider_unsupported_constraints() -> None:
    canonical = core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH)
    anthropic = anthropic_transport_schema(canonical)
    gemini = gemini_transport_schema(canonical)

    anthropic_keys = _schema_keys(anthropic)
    assert not {
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "uniqueItems",
    } & anthropic_keys
    assert {"$defs", "$ref", "properties", "required", "additionalProperties"} <= (
        anthropic_keys
    )
    assert {"pattern", "const", "anyOf", "enum", "items"} <= anthropic_keys

    gemini_keys = _schema_keys(gemini)
    assert not {"minLength", "maxLength", "pattern", "uniqueItems", "const"} & (
        gemini_keys
    )
    assert {"$defs", "$ref", "properties", "required", "additionalProperties"} <= (
        gemini_keys
    )
    assert {"anyOf", "enum", "items"} <= gemini_keys
    assert gemini["properties"]["schema_version"]["enum"] == [
        core.RESPONSE_SCHEMA_VERSION
    ]
    assert gemini["additionalProperties"] is False

    numeric_and_array = {
        "type": "object",
        "properties": {
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "values": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 2,
            },
        },
        "required": ["score", "values"],
        "additionalProperties": False,
    }
    assert "minimum" not in anthropic_transport_schema(numeric_and_array)[
        "properties"
    ]["score"]
    gemini_numeric = gemini_transport_schema(numeric_and_array)
    assert gemini_numeric["properties"]["score"]["minimum"] == 0
    assert gemini_numeric["properties"]["score"]["maximum"] == 1
    assert gemini_numeric["properties"]["values"]["minItems"] == 1
    assert gemini_numeric["properties"]["values"]["maxItems"] == 2


def test_local_validation_retains_canonical_min_length_and_unique_items() -> None:
    schema = core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH)
    empty_summary = response()
    empty_summary["review_summary"] = ""
    duplicate_concerns = response()
    duplicate_concerns["construct_validity_concerns"] = ["same", "same"]

    assert any(
        "non-empty" in error or "too short" in error
        for error in core.validation_errors(empty_summary, schema)
    )
    assert any(
        "non-unique" in error or "unique" in error
        for error in core.validation_errors(duplicate_concerns, schema)
    )


def test_blinded_payload_uses_allow_list_and_preserves_source_order() -> None:
    payload = core.build_blinded_payload(queue_record())

    assert payload["generation_group_id"] == "airisk_generation_group_0000"
    assert [item["occurrence_id"] for item in payload["contextualisations"]] == [
        "airisk_pair_00000",
        "airisk_pair_00001",
    ]
    assert payload["contextualisations"][0]["actions"]["action_1"]["action"] == (
        "Action 1: Exact source action 1"
    )
    assert payload["contextualisations"][0]["actions"]["action_1"]["targets"] == [
        "Human"
    ]
    assert core.find_forbidden_model_visible_keys(payload) == []


def test_exact_source_seed_keeps_question_but_blinds_upstream_answer_labels() -> None:
    source = queue_record()
    source["source_seed_match"] = {
        "status": "exact_publication_anchor",
        "confidence": "exact",
        "provenance": "anthropics/evals@revision",
        "source_seed_record": {
            "source_record_id": "advanced-ai-risk-1",
            "source_path": "advanced-ai-risk/dataset.jsonl",
            "source_row_index": 1,
            "source_dataset_layer": "publication_anchor",
            "question": "What is the exact neutral source question?",
            "question_sha256": "b" * 64,
            "answer_matching_behavior": "A",
            "answer_not_matching_behavior": "B",
        },
        "candidate_matches": [],
    }

    payload = core.build_blinded_payload(source)
    seed = payload["source_seed_lineage"]["exact_source_seed"]
    assert seed["question"] == "What is the exact neutral source question?"
    assert seed["source_path"] == "advanced-ai-risk/dataset.jsonl"
    assert "answer_matching_behavior" not in seed
    assert "answer_not_matching_behavior" not in seed
    assert core.find_forbidden_model_visible_keys(payload) == []
    core.validate_instance(
        payload,
        core.load_schema(core.DEFAULT_INPUT_SCHEMA_PATH),
        label="payload",
    )


def test_all_heuristic_ranking_and_prior_review_fields_cannot_leak() -> None:
    source = queue_record()
    source["all_contextualisations"][0]["actions"]["action_1"][
        "previous_reviewer_response"
    ] = {"h1": "yes"}
    payload = core.build_blinded_payload(source)
    rendered = core.canonical_json(payload)

    excluded_literals = [
        "heuristic_group_queue_priority_band",
        "heuristic_best_member_score",
        "heuristic_group_score_median",
        "heuristic_disclaimer",
        "representative_candidates",
        "deterministic_ranking_reasons",
        "reviewer_fields",
        "previous_reviewer_response",
        "semantic_family",
        "high_stakes_domain",
        "blocker_family_hint",
        "candidate_matches",
        "pre_outcome_audit_sample",
    ]
    assert all(f'"{literal}":' not in rendered for literal in excluded_literals)


def test_payload_and_response_validate_against_canonical_schemas() -> None:
    payload = core.build_blinded_payload(queue_record())
    core.validate_instance(
        payload,
        core.load_schema(core.DEFAULT_INPUT_SCHEMA_PATH),
        label="payload",
    )
    parsed = response()
    assert core.validate_review_response(
        parsed,
        payload,
        core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
    ) == []


def test_initial_review_cannot_fill_transformation_duplicate() -> None:
    parsed = response()
    parsed["likely_transformation_duplicate_of"] = "airisk_generation_group_0001"
    errors = core.validate_review_response(
        parsed,
        core.build_blinded_payload(queue_record()),
        core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
    )
    assert any("must remain null" in error or "not of type 'null'" in error for error in errors)


def test_selected_representative_must_belong_to_group() -> None:
    parsed = response()
    parsed["selected_representative_occurrence_id"] = "airisk_pair_99999"
    errors = core.validate_review_response(
        parsed,
        core.build_blinded_payload(queue_record()),
        core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
    )
    assert any("not a member" in error for error in errors)


def test_anthropic_adapter_uses_structured_output_schema_with_mock_client() -> None:
    expected = response()
    calls = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="msg-1",
                model="claude-reported",
                content=[SimpleNamespace(text=json.dumps(expected))],
            )

    adapter = AnthropicReviewerAdapter(client=SimpleNamespace(messages=Messages()))
    result = adapter.review(
        requested_model="claude-requested",
        rendered_prompt="frozen prompt and payload",
        response_schema=core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
        model_settings={"temperature": None, "max_output_tokens": 4096},
    )

    assert json.loads(result.raw_response_text) == expected
    assert result.resolved_reported_model == "claude-reported"
    assert calls[0]["messages"][0]["content"] == "frozen prompt and payload"
    assert calls[0]["output_config"]["format"]["type"] == "json_schema"
    assert calls[0]["output_config"]["format"]["schema"]["additionalProperties"] is False
    assert "minLength" not in _schema_keys(
        calls[0]["output_config"]["format"]["schema"]
    )
    assert "uniqueItems" not in _schema_keys(
        calls[0]["output_config"]["format"]["schema"]
    )
    assert "temperature" not in calls[0]


def test_gemini_adapter_uses_structured_output_schema_with_mock_client() -> None:
    expected = response()
    calls = []

    class Interactions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="interaction-1",
                model_version="gemini-reported",
                output_text=json.dumps(expected),
            )

    adapter = GeminiReviewerAdapter(client=SimpleNamespace(interactions=Interactions()))
    result = adapter.review(
        requested_model="gemini-requested",
        rendered_prompt="frozen prompt and payload",
        response_schema=core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
        model_settings={"temperature": None, "max_output_tokens": 4096},
    )

    assert json.loads(result.raw_response_text) == expected
    assert result.resolved_reported_model == "gemini-reported"
    assert calls[0]["input"] == "frozen prompt and payload"
    assert calls[0]["response_format"]["mime_type"] == "application/json"
    assert calls[0]["response_format"]["schema"]["additionalProperties"] is False
    assert not {"minLength", "pattern", "uniqueItems", "const"} & _schema_keys(
        calls[0]["response_format"]["schema"]
    )
    assert calls[0]["store"] is False
    assert "temperature" not in calls[0]["generation_config"]


def test_openrouter_key2_loads_from_environment_and_dotenv_without_leakage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("OPENROUTER_API_KEY2", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OPENROUTER_API_KEY2=dotenv-key2-secret\n"
        "OPENROUTER_API_KEY=dotenv-key-secret\n",
        encoding="utf-8",
    )
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(),
                model="anthropic/claude-sonnet-5",
                upstream_provider="Anthropic",
            )
        ]
    )
    adapter = OpenRouterReviewerAdapter(session=session, env_path=env_path)
    result = adapter.review(
        requested_model="anthropic/claude-sonnet-5",
        rendered_prompt="frozen prompt and payload",
        response_schema=core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
        model_settings={"temperature": None, "max_output_tokens": 4096},
    )

    assert session.calls[0]["headers"]["Authorization"] == (
        "Bearer dotenv-key2-secret"
    )
    assert "dotenv-key2-secret" not in core.canonical_json(result.raw_response)
    assert "dotenv-key-secret" not in core.canonical_json(result.raw_response)

    monkeypatch.setenv("OPENROUTER_API_KEY2", "environment-key2-secret")
    second_session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(),
                model="anthropic/claude-sonnet-5",
                upstream_provider="Anthropic",
            )
        ]
    )
    second_adapter = OpenRouterReviewerAdapter(
        session=second_session, env_path=env_path
    )
    second_adapter.review(
        requested_model="anthropic/claude-sonnet-5",
        rendered_prompt="frozen prompt and payload",
        response_schema=core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
        model_settings={"temperature": None, "max_output_tokens": 4096},
    )
    assert second_session.calls[0]["headers"]["Authorization"] == (
        "Bearer environment-key2-secret"
    )


def test_openrouter_falls_back_to_openrouter_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY2", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fallback-openrouter-secret")
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(),
                model="google/gemini-3.1-pro-preview",
                upstream_provider="Google AI Studio",
            )
        ]
    )
    adapter = OpenRouterReviewerAdapter(
        session=session, env_path=tmp_path / "absent.env"
    )
    adapter.review(
        requested_model="google/gemini-3.1-pro-preview",
        rendered_prompt="frozen prompt and payload",
        response_schema=core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
        model_settings={"temperature": None, "max_output_tokens": 4096},
    )

    assert session.calls[0]["headers"]["Authorization"] == (
        "Bearer fallback-openrouter-secret"
    )


@pytest.mark.parametrize(
    ("model", "upstream_provider", "transport_omissions", "transport_preserved"),
    [
        (
            "anthropic/claude-sonnet-5",
            "Anthropic",
            {"minLength", "maxLength", "minimum", "maximum", "uniqueItems"},
            {"pattern", "const"},
        ),
        (
            "google/gemini-3.1-pro-preview",
            "Google AI Studio",
            {"minLength", "maxLength", "pattern", "uniqueItems", "const"},
            {"$defs", "$ref", "anyOf", "enum"},
        ),
    ],
)
def test_openrouter_request_is_strict_and_selects_model_family_transport(
    model: str,
    upstream_provider: str,
    transport_omissions: set[str],
    transport_preserved: set[str],
) -> None:
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(), model=model, upstream_provider=upstream_provider
            )
        ]
    )
    adapter = OpenRouterReviewerAdapter(
        session=session, api_key="not-persisted-openrouter-secret"
    )
    result = adapter.review(
        requested_model=model,
        rendered_prompt="frozen prompt and payload",
        response_schema=core.load_schema(core.DEFAULT_RESPONSE_SCHEMA_PATH),
        model_settings={"temperature": None, "max_output_tokens": 4096},
    )

    call = session.calls[0]
    body = call["json"]
    response_format = body["response_format"]
    transport = response_format["json_schema"]["schema"]
    transport_keys = _schema_keys(transport)
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"]["X-OpenRouter-Metadata"] == "enabled"
    assert body["model"] == model
    assert "models" not in body
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == core.RESPONSE_SCHEMA_VERSION
    assert response_format["json_schema"]["strict"] is True
    assert body["provider"] == {"require_parameters": True}
    assert not transport_omissions & transport_keys
    assert transport_preserved <= transport_keys
    assert result.resolved_reported_model == model
    assert result.provider_request_id == "openrouter-request-1"
    assert result.provider_generation_id == "openrouter-generation-1"
    assert result.actual_routed_provider == upstream_provider
    assert result.provider_usage == {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }


def test_cli_max_output_tokens_accepts_8192() -> None:
    args = build_parser().parse_args(
        [
            "--provider",
            "openrouter",
            "--model",
            "anthropic/claude-sonnet-5",
            "--run-id",
            "openrouter-cli-token-limit",
            "--max-output-tokens",
            "8192",
        ]
    )
    assert args.max_output_tokens == 8192


def test_openrouter_no_execute_makes_no_request_and_needs_no_credential(
    tmp_path: Path,
) -> None:
    payload = core.build_blinded_payload(queue_record())
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [queue_record()])
    fake = FakeProvider([response()])

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "anthropic/claude-sonnet-5",
            "openrouter-safe-default",
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=False,
        resume=False,
        adapter=fake,
    )

    assert result["external_api_calls_planned"] == 0
    assert fake.calls == []
    assert not (tmp_path / "runs").exists()


def test_openrouter_runner_uses_full_canonical_local_validation(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    invalid = response()
    invalid["review_summary"] = ""
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                invalid,
                model="anthropic/claude-sonnet-5",
                upstream_provider="Anthropic",
            )
        ]
    )

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "anthropic/claude-sonnet-5",
            "openrouter-local-validation",
            max_retries=0,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(
            session=session, api_key="validation-test-secret"
        ),
        sleep_fn=lambda _: None,
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert "minLength" not in _schema_keys(
        session.calls[0]["json"]["response_format"]["json_schema"]["schema"]
    )
    assert record["status"] == "validation_failed"
    assert record["parsed_structured_response"] is None
    assert any(
        "non-empty" in error or "too short" in error
        for error in record["validation_errors"]
    )


def test_openrouter_run_record_preserves_routing_and_usage_without_key(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    secret = "must-not-be-persisted-openrouter-key"
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(),
                model="google/gemini-3.1-pro-preview",
                upstream_provider="Google AI Studio",
            )
        ]
    )

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "google/gemini-3.1-pro-preview",
            "openrouter-provenance",
            max_retries=0,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(session=session, api_key=secret),
        sleep_fn=lambda _: None,
    )

    record_text = Path(result["reviews_path"]).read_text(encoding="utf-8")
    manifest_text = Path(result["run_manifest_path"]).read_text(encoding="utf-8")
    record = json.loads(record_text)
    manifest = json.loads(manifest_text)
    assert secret not in record_text
    assert secret not in manifest_text
    assert record["provider"] == "openrouter"
    assert record["requested_model"] == "google/gemini-3.1-pro-preview"
    assert record["resolved_reported_model"] == "google/gemini-3.1-pro-preview"
    assert record["provider_request_id"] == "openrouter-request-1"
    assert record["provider_generation_id"] == "openrouter-generation-1"
    assert record["actual_routed_provider"] == "Google AI Studio"
    assert record["provider_usage"]["total_tokens"] == 150
    assert record["output_termination"] == {
        "finish_reason": "stop",
        "native_finish_reason": None,
        "output_limit_reached": False,
        "valid_complete_response_recovered": False,
    }
    assert record["retry_information"]["attempts"][0][
        "output_termination"
    ] == record["output_termination"]
    assert record["aggregate_usage"]["total_tokens"] == 150
    assert record["aggregate_usage"]["reported_attempt_counts"][
        "total_tokens"
    ] == 1
    assert record["prompt_sha256"] == core.sha256_path(core.DEFAULT_PROMPT_PATH)
    assert record["response_schema_sha256"] == core.sha256_path(
        core.DEFAULT_RESPONSE_SCHEMA_PATH
    )
    assert record["input_payload_sha256"] == core.input_payload_sha256(payload)
    assert record["model_settings"] == {
        "temperature": None,
        "max_output_tokens": 4096,
    }
    assert manifest["adapter_configuration"]["require_parameters"] is True
    assert manifest["adapter_configuration"][
        "alternate_model_fallbacks_requested"
    ] is False
    assert manifest["credential_source_order"] == [
        "OPENROUTER_API_KEY2",
        "OPENROUTER_API_KEY",
    ]


def test_openrouter_valid_length_terminated_response_completes_with_warning(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    reasoning_details = [{"type": "reasoning.text", "text": "Final check."}]
    usage = {
        "prompt_tokens": 120,
        "completion_tokens": 4096,
        "total_tokens": 4216,
        "cost": 0.061,
        "prompt_tokens_details": {"cached_tokens": 20},
        "completion_tokens_details": {"reasoning_tokens": 1000},
        "cost_details": {"upstream_inference_cost": 0.057},
    }
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(),
                model="google/gemini-3.1-pro-preview",
                upstream_provider="Google",
                finish_reason="length",
                native_finish_reason="MAX_TOKENS",
                reasoning="The complete JSON was emitted before the limit.",
                reasoning_details=reasoning_details,
                usage=usage,
            )
        ]
    )

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "google/gemini-3.1-pro-preview",
            "openrouter-valid-length",
            max_output_tokens=8192,
            max_retries=2,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(
            session=session, api_key="valid-length-secret"
        ),
        sleep_fn=lambda _: pytest.fail("A valid length-terminated response must not retry"),
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    warning = {
        "finish_reason": "length",
        "native_finish_reason": "MAX_TOKENS",
        "output_limit_reached": True,
        "valid_complete_response_recovered": True,
    }
    assert len(session.calls) == 1
    assert session.calls[0]["json"]["max_tokens"] == 8192
    assert record["status"] == "completed"
    assert record["parsed_structured_response"] == response()
    assert record["output_termination"] == warning
    assert record["retry_information"]["attempts"][0][
        "output_termination"
    ] == warning
    assert record["provider_reasoning"] == {
        "reasoning": "The complete JSON was emitted before the limit.",
        "reasoning_details": reasoning_details,
    }
    assert record["provider_usage"] == usage


def test_openrouter_invalid_partial_length_output_is_truncated_without_retry(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    partial_content = '{"schema_version":"airisk_jmcup_group_semantic_review_v1"'
    usage = {
        "prompt_tokens": 200,
        "completion_tokens": 4082,
        "total_tokens": 4282,
        "cost": 0.07,
        "prompt_tokens_details": {"cached_tokens": 30},
        "completion_tokens_details": {"reasoning_tokens": 3000},
        "cost_details": {"upstream_inference_cost": 0.066},
    }
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(),
                model="google/gemini-3.1-pro-preview",
                upstream_provider="Google",
                finish_reason="length",
                native_finish_reason="MAX_TOKENS",
                content=partial_content,
                reasoning="Reasoning reached the output ceiling.",
                reasoning_details=[{"type": "reasoning.text", "text": "Partial"}],
                usage=usage,
            )
        ]
    )

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "google/gemini-3.1-pro-preview",
            "openrouter-partial-length",
            max_retries=2,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(
            session=session, api_key="partial-length-secret"
        ),
        sleep_fn=lambda _: pytest.fail("Explicit output truncation must not retry"),
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert len(session.calls) == 1
    assert record["status"] == "truncated"
    assert record["parsed_structured_response"] is None
    assert record["raw_response_text"] == partial_content
    assert record["raw_response"]["body"]["choices"][0]["message"][
        "content"
    ] == partial_content
    assert record["provider_reasoning"]["reasoning"] == (
        "Reasoning reached the output ceiling."
    )
    assert record["output_termination"]["output_limit_reached"] is True
    assert record["output_termination"][
        "valid_complete_response_recovered"
    ] is False
    assert record["retry_information"]["attempts"][0][
        "output_termination"
    ] == record["output_termination"]
    assert record["retry_information"]["attempts"][0]["provider_usage"] == usage
    assert record["aggregate_usage"]["cost"] == pytest.approx(0.07)
    assert record["validation_errors"]


def test_openrouter_null_content_length_output_preserves_reasoning_without_retry(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    session = FakeOpenRouterSession(
        [
            openrouter_success(
                response(),
                model="anthropic/claude-sonnet-5",
                upstream_provider="Amazon Bedrock",
                finish_reason="length",
                native_finish_reason="max_tokens",
                content=None,
                reasoning="Reasoning was returned without assistant content.",
                reasoning_details=[],
                usage={
                    "prompt_tokens": 12745,
                    "completion_tokens": 4096,
                    "total_tokens": 16841,
                    "cost": 0.06645,
                    "prompt_tokens_details": {"cached_tokens": 0},
                    "completion_tokens_details": {"reasoning_tokens": 1484},
                },
            )
        ]
    )

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "anthropic/claude-sonnet-5",
            "openrouter-null-length",
            max_retries=2,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(
            session=session, api_key="null-length-secret"
        ),
        sleep_fn=lambda _: pytest.fail("Explicit output truncation must not retry"),
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert len(session.calls) == 1
    assert record["status"] == "truncated"
    assert record["raw_response_text"] is None
    assert record["provider_reasoning"] == {
        "reasoning": "Reasoning was returned without assistant content.",
        "reasoning_details": [],
    }
    assert record["output_termination"]["finish_reason"] == "length"
    assert record["output_termination"]["native_finish_reason"] == "max_tokens"


def test_openrouter_transient_retry_preserves_and_aggregates_attempt_costs(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    first_usage = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cost": 0.02,
        "prompt_tokens_details": {"cached_tokens": 3},
        "completion_tokens_details": {"reasoning_tokens": 2},
        "cost_details": {"upstream_inference_cost": 0.018},
    }
    second_usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "cost": 0.05,
        "prompt_tokens_details": {"cached_tokens": 20},
        "completion_tokens_details": {"reasoning_tokens": 10},
        "cost_details": {"upstream_inference_cost": 0.045},
    }
    first_body = {
        "id": "openrouter-error-request",
        "model": "anthropic/claude-sonnet-5",
        "provider": "Provider A",
        "error": {"code": 500, "message": "Temporary upstream failure"},
        "usage": first_usage,
    }
    session = FakeOpenRouterSession(
        [
            FakeOpenRouterResponse(
                first_body,
                status_code=500,
                headers={"X-Generation-Id": "generation-error"},
            ),
            openrouter_success(
                response(),
                model="anthropic/claude-sonnet-5",
                upstream_provider="Provider B",
                usage=second_usage,
                request_id="openrouter-success-request",
                generation_id="generation-success",
            ),
        ]
    )
    sleeps = []

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "anthropic/claude-sonnet-5",
            "openrouter-retry-costs",
            max_retries=1,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(
            session=session, api_key="retry-cost-secret"
        ),
        sleep_fn=sleeps.append,
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    attempts = record["retry_information"]["attempts"]
    assert len(session.calls) == 2
    assert sleeps == [2.0]
    assert record["status"] == "completed"
    assert [attempt["status"] for attempt in attempts] == [
        "provider_error",
        "completed",
    ]
    assert attempts[0]["provider_request_id"] == "openrouter-error-request"
    assert attempts[0]["provider_generation_id"] == "generation-error"
    assert attempts[0]["actual_routed_provider"] == "Provider A"
    assert attempts[0]["provider_usage"] == first_usage
    assert attempts[1]["provider_request_id"] == "openrouter-success-request"
    assert attempts[1]["provider_generation_id"] == "generation-success"
    assert attempts[1]["actual_routed_provider"] == "Provider B"
    assert attempts[1]["provider_usage"] == second_usage
    assert record["provider_usage"] == second_usage
    assert record["aggregate_usage"] == {
        "prompt_tokens": 110,
        "completion_tokens": 55,
        "reasoning_tokens": 12,
        "cached_tokens": 23,
        "total_tokens": 165,
        "cost": pytest.approx(0.07),
        "reported_attempt_counts": {
            "prompt_tokens": 2,
            "completion_tokens": 2,
            "reasoning_tokens": 2,
            "cached_tokens": 2,
            "total_tokens": 2,
            "cost": 2,
        },
    }


def test_openrouter_explicit_content_policy_block_is_not_retried(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    block_body = {
        "id": "openrouter-block-request",
        "model": "anthropic/claude-sonnet-5",
        "error": {
            "code": 400,
            "message": "Content policy blocked the request.",
            "metadata": {"error_type": "content_policy_violation"},
        },
        "openrouter_metadata": {
            "requested": "anthropic/claude-sonnet-5",
            "endpoints": {
                "available": [
                    {
                        "model": "anthropic/claude-sonnet-5",
                        "provider": "Anthropic",
                        "selected": True,
                    }
                ]
            },
        },
    }
    session = FakeOpenRouterSession(
        [FakeOpenRouterResponse(block_body, status_code=400)]
    )

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "anthropic/claude-sonnet-5",
            "openrouter-block",
            max_retries=3,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(
            session=session, api_key="block-test-secret"
        ),
        sleep_fn=lambda _: pytest.fail("A clear block must not sleep or retry"),
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert len(session.calls) == 1
    assert record["status"] == "blocked"
    assert record["parsed_structured_response"] is None
    assert record["provider_block_metadata"]["provider"] == "openrouter"
    assert record["provider_block_metadata"]["native_code"] == (
        "content_policy_violation"
    )
    assert record["raw_response"]["body"] == block_body
    assert record["retry_information"]["attempt_count"] == 1


def test_openrouter_ambiguous_forbidden_error_is_not_guessed_as_a_block(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    ambiguous_body = {
        "error": {
            "code": 403,
            "message": "Forbidden",
        }
    }
    session = FakeOpenRouterSession(
        [FakeOpenRouterResponse(ambiguous_body, status_code=403)]
    )

    result = run_reviews(
        config=ReviewRunConfig(
            "openrouter",
            "google/gemini-3.1-pro-preview",
            "openrouter-ambiguous-error",
            max_retries=0,
        ),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=OpenRouterReviewerAdapter(
            session=session, api_key="ambiguous-test-secret"
        ),
        sleep_fn=lambda _: None,
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert record["status"] == "provider_error"
    assert record["provider_block_metadata"] is None
    assert record["raw_response"]["body"] == ambiguous_body


def test_safe_default_makes_no_call_and_creates_no_run_directory(tmp_path: Path) -> None:
    payload = core.build_blinded_payload(queue_record())
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [queue_record()])
    fake = FakeProvider([response()])
    config = ReviewRunConfig("anthropic", "model", "safe-default")

    result = run_reviews(
        config=config,
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=False,
        resume=False,
        adapter=fake,
    )

    assert result["external_api_calls_planned"] == 0
    assert fake.calls == []
    assert not (tmp_path / "runs").exists()


def test_execution_stores_required_provenance_and_resume_skips_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    fake = FakeProvider([response()])
    config = ReviewRunConfig(
        "anthropic", "requested-model", "reviewer-a", max_retries=0
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-never-be-written")

    first = run_reviews(
        config=config,
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=fake,
        sleep_fn=lambda _: None,
    )
    second = run_reviews(
        config=config,
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=True,
        adapter=fake,
        sleep_fn=lambda _: None,
    )

    records_path = Path(first["reviews_path"])
    record = json.loads(records_path.read_text(encoding="utf-8"))
    assert record["reviewer_run_id"] == "reviewer-a"
    assert record["provider"] == "anthropic"
    assert record["requested_model"] == "requested-model"
    assert record["resolved_reported_model"] == "reported-model"
    assert record["prompt_sha256"] == core.sha256_path(core.DEFAULT_PROMPT_PATH)
    assert record["input_payload_sha256"] == core.input_payload_sha256(payload)
    assert record["parsed_structured_response"] == response()
    assert second["new_records_written"] == 0
    assert len(fake.calls) == 1
    assert "must-never-be-written" not in records_path.read_text(encoding="utf-8")
    assert "must-never-be-written" not in Path(first["run_manifest_path"]).read_text(
        encoding="utf-8"
    )


def test_validation_failure_is_retried_and_recorded(tmp_path: Path) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    invalid = response()
    invalid["criteria"][core.CRITERION_KEYS[0]]["judgement"] = "maybe"
    fake = FakeProvider([invalid, response()])
    config = ReviewRunConfig(
        "anthropic", "model", "retry-test", max_retries=1
    )

    result = run_reviews(
        config=config,
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=fake,
        sleep_fn=lambda _: None,
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert record["status"] == "completed"
    assert record["retry_information"]["attempt_count"] == 2
    assert record["retry_information"]["attempts"][0]["status"] == "validation_failed"
    assert record["retry_information"]["attempts"][1]["status"] == "completed"


def test_anthropic_refusal_is_recorded_without_automatic_retry(tmp_path: Path) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    calls = []
    sleeps = []

    class Messages:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                id="msg-refusal",
                model="claude-reported",
                content=[],
                stop_reason="refusal",
                stop_details=SimpleNamespace(
                    type="refusal",
                    category="bio",
                    explanation="Request declined by the safety classifier.",
                ),
            )

    result = run_reviews(
        config=ReviewRunConfig("anthropic", "model", "refusal-test", max_retries=3),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=AnthropicReviewerAdapter(client=SimpleNamespace(messages=Messages())),
        sleep_fn=sleeps.append,
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert sleeps == []
    assert record["status"] == "refused"
    assert record["raw_response"]["stop_reason"] == "refusal"
    assert record["raw_response_text"] is None
    assert record["parsed_structured_response"] is None
    assert record["provider_block_metadata"]["provider_signal"] == "stop_reason"
    assert record["provider_block_metadata"]["native_code"] == "refusal"
    assert record["provider_block_metadata"]["category"] == "bio"
    assert record["retry_information"]["attempt_count"] == 1
    assert record["retry_information"]["attempts"][0]["status"] == "refused"


def test_gemini_block_is_recorded_without_automatic_retry(tmp_path: Path) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    calls = []
    sleeps = []

    class GeminiBlockedError(RuntimeError):
        def __init__(self):
            super().__init__("Gemini generation was blocked")
            self.body = {
                "error": {
                    "code": "RECITATION",
                    "message": "Output was blocked for recitation.",
                }
            }
            self.request_id = "gemini-block-request"

    class Interactions:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise GeminiBlockedError()

    result = run_reviews(
        config=ReviewRunConfig("gemini", "model", "blocked-test", max_retries=3),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=GeminiReviewerAdapter(
            client=SimpleNamespace(interactions=Interactions())
        ),
        sleep_fn=sleeps.append,
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert len(calls) == 1
    assert sleeps == []
    assert record["status"] == "blocked"
    assert record["raw_response"]["body"]["error"]["code"] == "RECITATION"
    assert record["raw_response_text"] is None
    assert record["parsed_structured_response"] is None
    assert record["provider_block_metadata"]["native_code"] == "RECITATION"
    assert record["provider_block_metadata"]["category"] == "RECITATION"
    assert record["retry_information"]["attempt_count"] == 1
    assert record["retry_information"]["attempts"][0]["status"] == "blocked"


def test_gemini_transient_error_remains_a_retryable_provider_error(
    tmp_path: Path,
) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    calls = []

    class Interactions:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("429 rate limit")

    result = run_reviews(
        config=ReviewRunConfig("gemini", "model", "transient-test", max_retries=1),
        payloads=[payload],
        queue_path=queue,
        output_root=tmp_path / "runs",
        execute=True,
        resume=False,
        adapter=GeminiReviewerAdapter(
            client=SimpleNamespace(interactions=Interactions())
        ),
        sleep_fn=lambda _: None,
    )

    record = json.loads(Path(result["reviews_path"]).read_text(encoding="utf-8"))
    assert len(calls) == 2
    assert record["status"] == "provider_error"
    assert record["provider_block_metadata"] is None
    assert record["retry_information"]["attempt_count"] == 2


def test_separate_run_ids_are_stored_in_separate_directories(tmp_path: Path) -> None:
    source = queue_record()
    payload = core.build_blinded_payload(source)
    queue = tmp_path / "queue.jsonl"
    write_queue(queue, [source])
    for run_id in ("reviewer-a", "reviewer-b"):
        run_reviews(
            config=ReviewRunConfig(
                "anthropic", "model", run_id, max_retries=0
            ),
            payloads=[payload],
            queue_path=queue,
            output_root=tmp_path / "runs",
            execute=True,
            resume=False,
            adapter=FakeProvider([response()]),
            sleep_fn=lambda _: None,
        )

    assert (tmp_path / "runs" / "anthropic" / "reviewer-a" / "reviews.jsonl").is_file()
    assert (tmp_path / "runs" / "anthropic" / "reviewer-b" / "reviews.jsonl").is_file()


def test_consensus_clean_requires_both_all_yes_fidelity_and_viable_rewrite() -> None:
    assert classify_pair(response(), response(fidelity="moderate", rewrite="high")) == (
        "consensus_clean_candidate"
    )
    uncertain = response(judgements={core.CRITERION_KEYS[0]: "uncertain"})
    assert classify_pair(response(), uncertain) == "needs_adjudication"


def test_consensus_reject_requires_each_reject_signal_and_hard_failure() -> None:
    reject_a = response(
        judgements={core.CRITERION_KEYS[1]: "no"},
        rewrite="not_viable",
        verdict="reserve",
    )
    reject_b = response(
        judgements={core.CRITERION_KEYS[5]: "no"},
        rewrite="high",
        verdict="reject",
    )
    assert classify_pair(reject_a, reject_b) == "consensus_reject"
    no_hard_failure = response(rewrite="not_viable", verdict="reject")
    assert classify_pair(reject_a, no_hard_failure) == "needs_adjudication"


def test_comparison_flags_criterion_and_transformation_disagreements() -> None:
    parsed_a = response()
    parsed_b = response(
        judgements={core.CRITERION_KEYS[2]: "uncertain"},
        fidelity="low",
        rewrite="high",
    )
    parsed_b["proposed_baseline_blocker"] = "Completely different governance issue"
    comparison = compare_review_pair(
        review_record("reviewer-a", parsed_a),
        review_record("reviewer-b", parsed_b, provider="gemini"),
    )

    assert comparison["classification"] == "needs_adjudication"
    assert comparison["flags"]["h1_h7_disagreements"] == [core.CRITERION_KEYS[2]]
    assert comparison["flags"]["any_uncertain"] is True
    assert comparison["flags"]["source_fidelity_disagreement"] is True
    assert comparison["flags"]["rewrite_level_disagreement"] is True
    assert "proposed_baseline_blocker" in comparison["flags"][
        "materially_different_proposed_transformation_fields"
    ]


def test_adjudication_preparation_keeps_reviews_separate_and_does_not_average(
    tmp_path: Path,
) -> None:
    a_path = tmp_path / "a.jsonl"
    b_path = tmp_path / "b.jsonl"
    a_record = review_record("reviewer-a", response())
    b_record = review_record("reviewer-b", response(), provider="gemini")
    a_path.write_text(core.canonical_json(a_record) + "\n", encoding="utf-8")
    b_path.write_text(core.canonical_json(b_record) + "\n", encoding="utf-8")

    summary = prepare_adjudication(
        a_path,
        b_path,
        output_dir=tmp_path / "adjudication",
        expected_group_ids=["airisk_generation_group_0000"],
    )
    comparison = json.loads(
        (tmp_path / "adjudication" / "criterion_level_comparison.jsonl").read_text(
            encoding="utf-8"
        )
    )

    assert summary["classification_counts"] == {"consensus_clean_candidate": 1}
    assert summary["classification_rule"]["verdict_labels_averaged"] is False
    assert comparison["reviewer_a"]["reviewer_run_id"] == "reviewer-a"
    assert comparison["reviewer_b"]["reviewer_run_id"] == "reviewer-b"
    assert comparison["adjudicator_fields"]["final_classification"] is None


def test_adjudication_allows_two_openrouter_runs_with_different_models(
    tmp_path: Path,
) -> None:
    a_path = tmp_path / "openrouter-a.jsonl"
    b_path = tmp_path / "openrouter-b.jsonl"
    a_record = review_record("openrouter-claude", response(), provider="openrouter")
    b_record = review_record("openrouter-gemini", response(), provider="openrouter")
    a_record["requested_model"] = "anthropic/claude-sonnet-5"
    a_record["resolved_reported_model"] = "anthropic/claude-sonnet-5"
    b_record["requested_model"] = "google/gemini-3.1-pro-preview"
    b_record["resolved_reported_model"] = "google/gemini-3.1-pro-preview"
    a_path.write_text(core.canonical_json(a_record) + "\n", encoding="utf-8")
    b_path.write_text(core.canonical_json(b_record) + "\n", encoding="utf-8")

    summary = prepare_adjudication(
        a_path,
        b_path,
        output_dir=tmp_path / "openrouter-adjudication",
        expected_group_ids=["airisk_generation_group_0000"],
    )
    comparison = json.loads(
        (
            tmp_path
            / "openrouter-adjudication"
            / "criterion_level_comparison.jsonl"
        ).read_text(encoding="utf-8")
    )

    assert summary["group_count"] == 1
    assert comparison["reviewer_a"]["provider"] == "openrouter"
    assert comparison["reviewer_b"]["provider"] == "openrouter"
    assert comparison["reviewer_a"]["requested_model"].startswith("anthropic/")
    assert comparison["reviewer_b"]["requested_model"].startswith("google/")


def test_real_v3_queue_builds_1040_blinded_payloads_when_available() -> None:
    if not core.DEFAULT_V3_QUEUE_PATH.exists():
        pytest.skip("Generated v3 review queue is not available")
    records = core.read_jsonl(core.DEFAULT_V3_QUEUE_PATH)
    payloads = core.build_payload_corpus(records)

    assert len(payloads) == 1_040
    assert len({payload["generation_group_id"] for payload in payloads}) == 1_040
    assert all(core.find_forbidden_model_visible_keys(payload) == [] for payload in payloads)
    assert sum(len(payload["contextualisations"]) for payload in payloads) == 10_399
