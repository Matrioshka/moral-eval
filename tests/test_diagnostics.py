from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from moral_sycophancy_eval.diagnostics import (  # noqa: E402
    PROVIDER_SUMMARY_MODE,
    USAGE_ONLY_MODE,
    discover_usage_locations,
    extract_model_call_diagnostics_from_raw_sample,
    extract_response_diagnostics,
    normalise_diagnostics_config,
    response_diagnostic_records_from_inspect_sample,
)
from run_experiment_pipeline import (  # noqa: E402
    MODEL_CALL_DIAGNOSTIC_UPSERT_SQL,
    RESPONSE_DIAGNOSTIC_UPSERT_SQL,
    build_inspect_command,
)


def amended_config() -> dict:
    return {
        "usage_metadata": {
            "enabled": True,
            "store_raw_provider_usage": True,
            "store_input_tokens": True,
            "store_output_tokens": True,
            "store_total_tokens": True,
            "store_reasoning_tokens_if_available": True,
            "store_thinking_tokens_if_available": True,
        },
        "provider_reasoning_summary": {
            "extract_if_present": False,
            "request_from_provider": False,
            "include_in_dialogue_context": False,
            "headline_eligible": False,
            "capture_after_turns": ["baseline", "pressure_1", "pressure_2", "pressure_3"],
        },
    }


def test_diagnostics_config_defaults_to_usage_only_enabled() -> None:
    config = normalise_diagnostics_config(None)

    assert config["usage_metadata"]["enabled"] is True
    assert config["provider_reasoning_summary"]["extract_if_present"] is False
    assert config["provider_reasoning_summary"]["request_from_provider"] is False


def test_amended_diagnostics_config_shape_is_accepted() -> None:
    config = normalise_diagnostics_config(amended_config())

    assert config["usage_metadata"]["store_total_tokens"] is True
    assert config["provider_reasoning_summary"]["capture_after_turns"] == [
        "baseline",
        "pressure_1",
        "pressure_2",
        "pressure_3",
    ]


def test_requesting_provider_reasoning_summary_is_rejected_for_v1() -> None:
    config = amended_config()
    config["provider_reasoning_summary"]["request_from_provider"] = True

    with pytest.raises(ValueError, match="not implemented in v1"):
        normalise_diagnostics_config(config)


def test_provider_summary_cannot_be_headline_eligible() -> None:
    config = amended_config()
    config["provider_reasoning_summary"]["headline_eligible"] = True

    with pytest.raises(ValueError, match="headline-eligible"):
        normalise_diagnostics_config(config)


def test_provider_summary_cannot_be_visible_to_later_dialogue_turns() -> None:
    config = amended_config()
    config["provider_reasoning_summary"]["include_in_dialogue_context"] = True

    with pytest.raises(ValueError, match="dialogue context"):
        normalise_diagnostics_config(config)


def test_prompted_rationale_diagnostics_are_rejected() -> None:
    with pytest.raises(ValueError, match="prompted rationale"):
        normalise_diagnostics_config({"prompted_rationale": {"enabled": True}})


def test_usage_metadata_extraction_handles_missing_provider_fields() -> None:
    records = extract_response_diagnostics(usage={}, raw_sample={}, diagnostics_config=None)

    assert len(records) == 1
    record = records[0]
    assert record["diagnostic_mode"] == USAGE_ONLY_MODE
    assert record["input_tokens"] is None
    assert record["output_tokens"] is None
    assert record["total_tokens"] is None
    assert record["reasoning_tokens"] is None
    assert record["thinking_tokens"] is None
    assert record["raw_usage_json"] is None


def test_usage_metadata_extraction_does_not_mutate_prompt_or_messages() -> None:
    raw_sample = {
        "input": "Should we approve release?",
        "messages": [{"role": "user", "content": "Initial prompt"}],
        "output": {"usage": {"input_tokens": 10, "output_tokens": 5}},
    }
    before = copy.deepcopy(raw_sample)

    extract_response_diagnostics(raw_sample=raw_sample, diagnostics_config=None)

    assert raw_sample == before


def test_provider_summary_extract_if_present_is_not_headline_eligible() -> None:
    config = amended_config()
    config["provider_reasoning_summary"]["extract_if_present"] = True
    raw_sample = {
        "output": {
            "provider_metadata": {"reasoning_summary": {"summary": "provider-supplied diagnostic"}}
        }
    }

    records = extract_response_diagnostics(usage={}, raw_sample=raw_sample, diagnostics_config=config)
    provider_records = [record for record in records if record["diagnostic_mode"] == PROVIDER_SUMMARY_MODE]

    assert len(provider_records) == 1
    assert provider_records[0]["headline_eligible"] is False
    assert provider_records[0]["visible_to_model_next_turn"] is False
    assert provider_records[0]["reasoning_summary_requested"] is False


def test_response_diagnostic_records_carry_linked_response_id() -> None:
    records = response_diagnostic_records_from_inspect_sample(
        {
            "response_id": 123,
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            "raw_sample": {},
        }
    )

    assert len(records) == 1
    assert records[0]["response_id"] == 123
    assert records[0]["input_tokens"] == 2


def test_response_diagnostic_upsert_is_idempotent_by_response_mode_version() -> None:
    assert "ON CONFLICT (response_id, diagnostic_mode, diagnostic_version)" in RESPONSE_DIAGNOSTIC_UPSERT_SQL


def test_diagnostics_do_not_change_inspect_command() -> None:
    base = {
        "task": "moral_sycophancy_eval/behaviour.py@moral_reasoning_integrity_behaviour",
        "dataset_version": "v5_multistage_pressure_pilot_v0",
        "model": "openai/test",
    }
    with_diagnostics = dict(base)
    with_diagnostics["diagnostics"] = normalise_diagnostics_config(None)

    assert build_inspect_command(with_diagnostics) == build_inspect_command(base)


def test_discovery_detects_aggregate_sample_level_usage_only() -> None:
    locations = discover_usage_locations(
        {
            "output": {"usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}},
            "model_usage": {"openai/test": {"input_tokens": 10, "output_tokens": 3}},
        }
    )

    assert {location["path"] for location in locations} >= {"output.usage", "model_usage"}
    assert all(not location["confirmed_call_level"] for location in locations)


def test_discovery_detects_per_event_model_call_usage() -> None:
    locations = discover_usage_locations(
        {
            "events": [
                {"event": "sample_init"},
                {
                    "event": "model",
                    "output": {
                        "completion": "Release should remain paused.",
                        "usage": {"input_tokens": 20, "output_tokens": 7},
                    },
                },
            ]
        }
    )

    event_locations = [location for location in locations if location["source_scope"] == "event"]
    assert len(event_locations) == 1
    assert event_locations[0]["path"] == "events.1.output.usage"
    assert event_locations[0]["confirmed_call_level"] is True


def test_model_call_extractor_returns_no_rows_for_aggregate_only_usage() -> None:
    records = extract_model_call_diagnostics_from_raw_sample(
        {
            "experiment_pipeline_run_id": 1,
            "inspect_log_sample_id": 2,
            "raw_sample": {"output": {"usage": {"input_tokens": 5, "output_tokens": 2}}},
        }
    )

    assert records == []


def test_model_call_extractor_returns_rows_for_confirmed_event_usage() -> None:
    records = extract_model_call_diagnostics_from_raw_sample(
        {
            "experiment_pipeline_run_id": 1,
            "inspect_log_sample_id": 2,
            "raw_sample": {
                "events": [
                    {
                        "event": "model",
                        "output": {
                            "completion": "Keep the concern calibrated.",
                            "usage": {
                                "input_tokens": 30,
                                "output_tokens": 8,
                                "completion_tokens_details": {"reasoning_tokens": 1},
                            },
                        },
                    }
                ]
            },
        }
    )

    assert len(records) == 1
    record = records[0]
    assert record["experiment_pipeline_run_id"] == 1
    assert record["inspect_log_sample_id"] == 2
    assert record["source_scope"] == "event"
    assert record["source_event_index"] == 0
    assert record["model_call_index"] == 0
    assert record["input_tokens"] == 30
    assert record["output_tokens"] == 8
    assert record["reasoning_tokens"] == 1
    assert record["thinking_tokens"] is None
    assert record["headline_eligible"] is False
    assert record["link_confidence"] == "unverified"
    assert record["link_method"] == "raw_event_only"


def test_ambiguous_message_usage_is_discovered_but_not_inserted() -> None:
    raw_sample = {
        "messages": [
            {
                "role": "assistant",
                "content": "A response",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        ]
    }

    locations = discover_usage_locations(raw_sample)
    records = extract_model_call_diagnostics_from_raw_sample(
        {"experiment_pipeline_run_id": 1, "inspect_log_sample_id": 2, "raw_sample": raw_sample}
    )

    assert len(locations) == 1
    assert locations[0]["path"] == "messages.0.usage"
    assert locations[0]["confirmed_call_level"] is False
    assert records == []


def test_confirmed_model_call_message_usage_can_be_inserted() -> None:
    raw_sample = {
        "messages": [
            {
                "role": "assistant",
                "source": "model_call",
                "content": "A response",
                "usage": {"input_tokens": 10, "output_tokens": 2},
            }
        ]
    }

    records = extract_model_call_diagnostics_from_raw_sample(
        {"experiment_pipeline_run_id": 1, "inspect_log_sample_id": 2, "raw_sample": raw_sample}
    )

    assert len(records) == 1
    assert records[0]["source_scope"] == "message"
    assert records[0]["input_tokens"] == 10
    assert records[0]["headline_eligible"] is False


def test_exact_assistant_text_linkage_when_unambiguous() -> None:
    records = extract_model_call_diagnostics_from_raw_sample(
        {
            "experiment_pipeline_run_id": 1,
            "inspect_log_sample_id": 2,
            "response_id": 99,
            "eval_case_id": 88,
            "case_turn_id": 77,
            "response_text": "Release should remain paused.",
            "raw_sample": {
                "events": [
                    {
                        "event": "model",
                        "output": {
                            "completion": "Release should remain paused.",
                            "usage": {"input_tokens": 20, "output_tokens": 5},
                        },
                    }
                ]
            },
        }
    )

    assert len(records) == 1
    assert records[0]["response_id"] == 99
    assert records[0]["eval_case_id"] == 88
    assert records[0]["case_turn_id"] == 77
    assert records[0]["link_confidence"] == "exact"
    assert records[0]["link_method"] == "assistant_text_and_turn_order"


def test_model_call_upsert_uses_non_nullable_source_key() -> None:
    expected = """ON CONFLICT (
    experiment_pipeline_run_id,
    inspect_log_sample_id,
    diagnostic_version,
    diagnostic_mode,
    source_scope,
    source_event_index,
    model_call_index
)"""
    assert expected in MODEL_CALL_DIAGNOSTIC_UPSERT_SQL


def test_model_call_diagnostic_migration_uses_sentinel_defaults() -> None:
    migration = (ROOT / "sql" / "022_create_model_call_diagnostics.sql").read_text(encoding="utf-8")

    assert "source_event_index integer NOT NULL DEFAULT -1" in migration
    assert "model_call_index integer NOT NULL DEFAULT -1" in migration
    assert "experiment_pipeline_run_id bigint NOT NULL" in migration
    assert "inspect_log_sample_id bigint NOT NULL" in migration
