from __future__ import annotations

import json
from pathlib import Path

import pytest

from moral_eval.pressure_trajectory.adapters.existing_datasets import load_trajectory
from moral_eval.pressure_trajectory.analysis import (
    analyse_event_records,
    calculate_mapping_diagnostics,
    qualify_explicit_gate_resolution_control,
)
from moral_eval.pressure_trajectory.events import json_compatible
from scripts import analyse_pressure_trajectory as analysis_cli

ROOT = Path(__file__).resolve().parents[2]
DATASET = (
    ROOT
    / "data"
    / "datasets"
    / "action_logprob"
    / "action_logprob_positive_control_gate_v0.jsonl"
)
FIXTURE = ROOT / "experiments" / "pressure_trajectory_mvp_v1.json"
CASE_ID = "deployment_gate__autonomous_cyber_defence_pilot_001"


def loaded_main():
    return load_trajectory(
        dataset_path=DATASET,
        case_id=CASE_ID,
        fixture_path=FIXTURE,
    )


def roles(loaded) -> dict[str, str]:
    return {
        action.action_id: action.semantic_role
        for action in loaded.scenario.action_choices
    }


def measurement(mapping, margin: float, timing: str = "pre_response") -> dict:
    if mapping.broader_label == "B":
        raw_bounded, raw_broader = 0.0, margin
    else:
        raw_bounded, raw_broader = 0.0, margin
    return {
        "measurement_version": "pressure_trajectory_measurement_v2",
        "measurement_timing": timing,
        "mapping_id": mapping.mapping_id,
        "mapped_margin": margin,
        "raw_bounded_logit": raw_bounded,
        "raw_broader_logit": raw_broader,
        "checkpoint_id": "run:checkpoint:0",
    }


def factors(mapping, action_roles: dict[str, str]) -> tuple[float, float]:
    label = 1.0 if mapping.broader_label == "B" else -1.0
    first_role = action_roles[mapping.ordered_action_ids[0]]
    first_label = (
        mapping.bounded_label if first_role == "bounded" else mapping.broader_label
    )
    order = 1.0 if first_label == "A" else -1.0
    return label, order


def test_actual_mapping_definitions_form_the_expected_balanced_two_by_two() -> None:
    loaded = loaded_main()
    action_roles = roles(loaded)
    expected = {
        "bounded_A_broader_B": ("B", "A", "bounded", 1.0, 1.0, 1.0),
        "broader_A_bounded_B": ("A", "B", "broader", -1.0, 1.0, -1.0),
        "bounded_A_broader_B__B_then_A": (
            "B",
            "A",
            "broader",
            1.0,
            -1.0,
            -1.0,
        ),
        "broader_A_bounded_B__B_then_A": (
            "A",
            "B",
            "bounded",
            -1.0,
            -1.0,
            1.0,
        ),
    }

    actual = {}
    for mapping in loaded.option_mappings:
        label_factor, order_factor = factors(mapping, action_roles)
        actual[mapping.mapping_id] = (
            mapping.broader_label,
            mapping.bounded_label,
            action_roles[mapping.ordered_action_ids[0]],
            label_factor,
            order_factor,
            label_factor * order_factor,
        )

    assert actual == expected


def test_balanced_two_by_two_decomposition_recovers_known_components() -> None:
    loaded = loaded_main()
    action_roles = roles(loaded)
    semantic, label, order, interaction = 2.0, 1.0, 0.5, 0.25
    measurements = []
    expected_raw_b_a = []
    for mapping in loaded.option_mappings:
        label_factor, order_factor = factors(mapping, action_roles)
        margin = (
            semantic
            + label * label_factor
            + order * order_factor
            + interaction * label_factor * order_factor
        )
        measurements.append(measurement(mapping, margin))
        expected_raw_b_a.append(label_factor * margin)

    result = calculate_mapping_diagnostics(
        measurements, loaded.option_mappings, action_roles
    )

    assert result.mean_semantic_mapped_margin == pytest.approx(semantic)
    assert result.median_mapped_margin == pytest.approx(1.75)
    assert result.fixed_b_label_preference_estimate == pytest.approx(label)
    assert result.a_first_vs_b_first_order_estimate == pytest.approx(order)
    assert result.label_by_order_interaction_estimate == pytest.approx(interaction)
    assert result.raw_b_minus_a == pytest.approx(expected_raw_b_a)
    assert result.sign_counts == {"positive": 4, "negative": 0, "tied": 0}
    assert result.mapping_sign_agreement == 1.0
    assert result.minimum_mapped_margin == min(
        item["mapped_margin"] for item in measurements
    )
    assert result.maximum_mapped_margin == max(
        item["mapped_margin"] for item in measurements
    )


def test_positive_control_qualification_pass_and_fail() -> None:
    loaded = loaded_main()
    action_roles = roles(loaded)
    unresolved = [
        measurement(mapping, margin)
        for mapping, margin in zip(
            loaded.option_mappings, (-2.0, -1.0, -3.0, -2.0), strict=True
        )
    ]
    resolved = [
        measurement(mapping, margin)
        for mapping, margin in zip(
            loaded.option_mappings, (2.0, 1.0, 3.0, 2.0), strict=True
        )
    ]

    passed = qualify_explicit_gate_resolution_control(
        unresolved, resolved, loaded.option_mappings, action_roles
    )
    failed = qualify_explicit_gate_resolution_control(
        resolved, unresolved, loaded.option_mappings, action_roles
    )

    assert passed.unresolved_balanced_mean == -2.0
    assert passed.resolved_balanced_mean == 2.0
    assert passed.resolution_effect == 4.0
    assert passed.at_least_three_unresolved_mappings_agree
    assert passed.at_least_three_resolved_mappings_agree
    assert passed.qualified_for_pressure_interpretation
    assert not failed.qualified_for_pressure_interpretation
    assert not failed.resolution_effect_positive


def test_legacy_v1_analysis_is_post_response_only(tmp_path, capsys) -> None:
    loaded = loaded_main()
    mappings = json_compatible(loaded.option_mappings)
    scenario = json_compatible(loaded.scenario)
    measurements = [
        measurement(mapping, margin, "post_response")
        for mapping, margin in zip(
            loaded.option_mappings, (-1.0, -0.5, -1.5, -1.0), strict=True
        )
    ]
    for item in measurements:
        item["measurement_version"] = "pressure_trajectory_measurement_v1"
        item.pop("measurement_timing")
    records = [
        {
            "event_type": "run_created",
            "payload": {
                "run_metadata": {
                    "trajectory_id": "legacy-trajectory",
                    "experiment_configuration_sha256": "legacy-hash",
                }
            },
        },
        {
            "event_type": "scenario_loaded",
            "payload": {"scenario": scenario, "option_mappings": mappings},
        },
        {
            "event_type": "baseline_response_generated",
            "payload": {
                "response": "Legacy response",
                "checkpoint": {
                    "checkpoint_id": "run:checkpoint:0",
                    "round_index": 0,
                },
            },
        },
        *(
            {
                "event_type": "measurement_recorded",
                "payload": {"measurement": item},
            }
            for item in measurements
        ),
        {
            "event_type": "run_completed",
            "payload": {
                "checkpoint_count": 1,
                "measurement_count": 4,
                "event_count": 8,
            },
        },
    ]

    result = analyse_event_records(records, file_sha256="file-hash")

    assert result["legacy_v1_post_response_only"]
    assert result["structural_status"] == "completed"
    assert "post-response only" in result["legacy_note"]
    assert result["file_sha256"] == "file-hash"
    assert result["experiment_configuration_sha256"] == "legacy-hash"
    assert "pre_response" not in result["rounds"][0]["measurements"]
    assert result["rounds"][0]["post_minus_pre_self_anchoring_shift"] is None

    path = tmp_path / "legacy-v1.jsonl"
    path.write_text(
        "\n".join(json.dumps(item) for item in records) + "\n",
        encoding="utf-8",
    )
    assert analysis_cli.main([str(path)]) == 0
    cli_output = json.loads(capsys.readouterr().out)
    assert cli_output["legacy_v1_post_response_only"]
    assert cli_output["file_sha256"] != "file-hash"

    incomplete = analyse_event_records(records[:-1], file_sha256="partial-hash")
    assert incomplete["structural_status"] == "incomplete"
    assert incomplete["diagnostic_only"]
    assert incomplete["rounds"][0]["measurements"]["post_response"][
        "diagnostic_only"
    ]
    assert incomplete["rounds"][0]["post_minus_pre_self_anchoring_shift"] is None
    assert incomplete["positive_control_qualification"] is None


def test_legacy_resolution_control_reports_descriptive_failure_without_v2_gate() -> None:
    loaded = loaded_main()
    mappings = json_compatible(loaded.option_mappings)
    scenario = json_compatible(loaded.scenario)
    round_margins = (
        (3.625, 1.5, 3.5, -1.25),
        (1.125, -1.25, 4.0, -3.125),
    )
    round_measurements = []
    for round_index, margins in enumerate(round_margins):
        items = [
            measurement(mapping, margin, "post_response")
            for mapping, margin in zip(
                loaded.option_mappings, margins, strict=True
            )
        ]
        for item in items:
            item["measurement_version"] = "pressure_trajectory_measurement_v1"
            item.pop("measurement_timing")
            item["checkpoint_id"] = f"legacy:checkpoint:{round_index}"
        round_measurements.append(items)

    records = [
        {
            "event_type": "run_created",
            "payload": {
                "run_metadata": {
                    "trajectory_id": "autonomous_cyber_gate_resolution_control_v0",
                    "experiment_configuration_sha256": "legacy-control-hash",
                    "experiment_configuration": {
                        "measurement": {"timing": "post_response"}
                    },
                }
            },
        },
        {
            "event_type": "scenario_loaded",
            "payload": {"scenario": scenario, "option_mappings": mappings},
        },
        {
            "event_type": "baseline_response_generated",
            "payload": {
                "response": "Legacy unresolved response",
                "checkpoint": {
                    "checkpoint_id": "legacy:checkpoint:0",
                    "round_index": 0,
                },
            },
        },
        *(
            {"event_type": "measurement_recorded", "payload": {"measurement": item}}
            for item in round_measurements[0]
        ),
        {"event_type": "pressure_turn_added", "payload": {}},
        {
            "event_type": "model_response_generated",
            "payload": {
                "response": "Legacy resolved response",
                "checkpoint": {
                    "checkpoint_id": "legacy:checkpoint:1",
                    "round_index": 1,
                },
            },
        },
        *(
            {"event_type": "measurement_recorded", "payload": {"measurement": item}}
            for item in round_measurements[1]
        ),
        {
            "event_type": "run_completed",
            "payload": {
                "checkpoint_count": 2,
                "measurement_count": 8,
                "event_count": 14,
            },
        },
    ]

    result = analyse_event_records(records, file_sha256="legacy-control-file")

    assert result["structural_status"] == "completed"
    assert result["legacy_v1_post_response_only"]
    assert result["positive_control_qualification"] is None
    comparison = result["positive_control_descriptive_comparison"]
    assert comparison == {
        "measurement_timing": "post_response",
        "unresolved_balanced_mean": 1.84375,
        "resolved_balanced_mean": 0.1875,
        "resolved_minus_unresolved": -1.65625,
        "outcome": "failed_direction",
        "qualification_applied": False,
    }
    assert "was not applied" in result["positive_control_qualification_note"]
    assert [item["generation"] for item in result["rounds"]] == [None, None]
    assert [item["response_excerpt"] for item in result["rounds"]] == [
        "Legacy unresolved response",
        "Legacy resolved response",
    ]

    partial = analyse_event_records(records[:-1], file_sha256="partial-control")
    assert partial["structural_status"] == "incomplete"
    assert partial["diagnostic_only"]
    assert partial["positive_control_descriptive_comparison"] is None
    assert partial["positive_control_qualification"] is None
