"""Stable experiment-configuration provenance."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .domain import GenerationSettings, OptionMapping, TrajectoryScenario
from .events import canonical_json_hash, json_compatible

EXPERIMENT_CONFIGURATION_SCHEMA_VERSION = "pressure_trajectory_experiment_config_v1"


def build_experiment_configuration(
    *,
    scenario: TrajectoryScenario,
    option_mappings: Sequence[OptionMapping],
    model_id: str,
    requested_model_revision: str | None,
    tokenizer_id: str,
    requested_tokenizer_revision: str | None,
    generation_settings: GenerationSettings,
    requested_device: str,
    requested_dtype: str,
    measurement_prompt_version: str,
    measurement_timing: str,
    backend_implementation: Mapping[str, str],
) -> dict[str, Any]:
    """Return only stable values that define or materially affect the experiment."""
    source = scenario.source_metadata
    required_source_fields = (
        "dataset_version",
        "dataset_sha256",
        "case_id",
        "fixture_schema_version",
        "fixture_version",
        "fixture_sha256",
        "trajectory_id",
    )
    missing = [field for field in required_source_fields if field not in source]
    if missing:
        raise ValueError(f"Scenario source metadata is missing fields: {missing}")
    configuration = {
        "schema_version": EXPERIMENT_CONFIGURATION_SCHEMA_VERSION,
        "dataset": {
            "dataset_version": source["dataset_version"],
            "dataset_sha256": source["dataset_sha256"],
            "case_id": source["case_id"],
        },
        "trajectory": {
            "trajectory_id": source["trajectory_id"],
            "fixture_schema_version": source["fixture_schema_version"],
            "fixture_version": source["fixture_version"],
            "fixture_sha256": source["fixture_sha256"],
        },
        "model": {
            "model_id": model_id,
            "requested_model_revision": requested_model_revision,
            "tokenizer_id": tokenizer_id,
            "requested_tokenizer_revision": requested_tokenizer_revision,
            "requested_device": requested_device,
            "requested_dtype": requested_dtype,
        },
        "generation": generation_settings,
        "measurement": {
            "prompt_version": measurement_prompt_version,
            "timing": measurement_timing,
        },
        "mappings": [
            {
                "mapping_id": mapping.mapping_id,
                "bounded_label": mapping.bounded_label,
                "broader_label": mapping.broader_label,
                "ordered_action_ids": mapping.ordered_action_ids,
            }
            for mapping in option_mappings
        ],
        "backend_implementation": dict(backend_implementation),
    }
    return json_compatible(configuration)


def experiment_configuration_sha256(configuration: Mapping[str, Any]) -> str:
    """Hash an experiment configuration using the repository's canonical JSON."""
    return canonical_json_hash(configuration)


def validate_experiment_configuration(
    configuration: Mapping[str, Any],
    *,
    scenario: TrajectoryScenario,
    option_mappings: Sequence[OptionMapping],
    generation_settings: GenerationSettings,
    measurement_prompt_version: str,
    measurement_timing: str,
) -> None:
    """Ensure recorded stable configuration agrees with the run being executed."""
    source = scenario.source_metadata
    expected_sections = {
        "schema_version": EXPERIMENT_CONFIGURATION_SCHEMA_VERSION,
        "dataset": {
            "dataset_version": source.get("dataset_version"),
            "dataset_sha256": source.get("dataset_sha256"),
            "case_id": source.get("case_id"),
        },
        "trajectory": {
            "trajectory_id": source.get("trajectory_id"),
            "fixture_schema_version": source.get("fixture_schema_version"),
            "fixture_version": source.get("fixture_version"),
            "fixture_sha256": source.get("fixture_sha256"),
        },
        "generation": json_compatible(generation_settings),
        "measurement": {
            "prompt_version": measurement_prompt_version,
            "timing": measurement_timing,
        },
        "mappings": [
            {
                "mapping_id": mapping.mapping_id,
                "bounded_label": mapping.bounded_label,
                "broader_label": mapping.broader_label,
                "ordered_action_ids": list(mapping.ordered_action_ids),
            }
            for mapping in option_mappings
        ],
    }
    for field, expected in expected_sections.items():
        if configuration.get(field) != expected:
            raise ValueError(
                f"Experiment configuration {field!r} does not match the run inputs"
            )
    model = configuration.get("model")
    if not isinstance(model, Mapping):
        raise ValueError("Experiment configuration 'model' must be an object")
    required_model_fields = {
        "model_id",
        "requested_model_revision",
        "tokenizer_id",
        "requested_tokenizer_revision",
        "requested_device",
        "requested_dtype",
    }
    if set(model) != required_model_fields:
        raise ValueError(
            "Experiment configuration 'model' must contain the stable model, "
            "tokenizer, device, and dtype fields"
        )
    backend = configuration.get("backend_implementation")
    if not isinstance(backend, Mapping) or not backend:
        raise ValueError(
            "Experiment configuration 'backend_implementation' must be non-empty"
        )
