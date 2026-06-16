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
    extract_response_diagnostics,
    normalise_diagnostics_config,
    response_diagnostic_records_from_inspect_sample,
)
from run_experiment_pipeline import (  # noqa: E402
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
