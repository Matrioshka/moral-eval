from __future__ import annotations

import builtins
import json
from dataclasses import replace
from pathlib import Path

import pytest

from moral_eval.pressure_trajectory.adapters.existing_datasets import load_trajectory
from moral_eval.pressure_trajectory.domain import (
    GenerationSettings,
    RunMetadata,
    TOKEN_SELECTION_POLICY,
)
from moral_eval.pressure_trajectory.measurements import (
    MEASUREMENT_PROMPT_VERSION,
    MEASUREMENT_TIMING,
)
from moral_eval.pressure_trajectory.provenance import (
    build_experiment_configuration,
    experiment_configuration_sha256,
)
from scripts import pressure_trajectory as cli

ROOT = Path(__file__).resolve().parents[2]
DATASET = (
    ROOT
    / "data"
    / "datasets"
    / "action_logprob"
    / "action_logprob_positive_control_gate_v0.jsonl"
)
FIXTURE = ROOT / "experiments" / "pressure_trajectory_mvp_v0.json"
CASE_ID = "deployment_gate__autonomous_cyber_defence_pilot_001"


class TokenizerOnlyFake:
    chat_template = "test"

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    @staticmethod
    def render(messages: list[dict[str, str]]) -> str:
        return "".join(
            f"<{message['role']}>{message['content']}" for message in messages
        ) + "<assistant>"

    def apply_chat_template(
        self,
        messages: list[dict[str, str]],
        *,
        tokenize: bool,
        add_generation_prompt: bool,
        return_dict: bool = False,
        **_: object,
    ):
        assert add_generation_prompt
        self.calls.append(messages)
        rendered = self.render(messages)
        if not tokenize:
            return rendered
        ids = self.encode(rendered, add_special_tokens=False)
        return {"input_ids": ids} if return_dict else ids

    @staticmethod
    def encode(text: str, *, add_special_tokens: bool) -> list[int]:
        assert not add_special_tokens
        return [ord(character) for character in text]

    @staticmethod
    def decode(ids: list[int], **_: object) -> str:
        return "".join(chr(item) for item in ids)


def loaded_main():
    return load_trajectory(
        dataset_path=DATASET,
        case_id=CASE_ID,
        fixture_path=FIXTURE,
    )


def configuration(
    loaded,
    *,
    revision: str = "revision-1",
    settings: GenerationSettings | None = None,
    prompt_version: str = MEASUREMENT_PROMPT_VERSION,
    token_selection_policy: str = TOKEN_SELECTION_POLICY,
    mappings=None,
):
    return build_experiment_configuration(
        scenario=loaded.scenario,
        option_mappings=mappings or loaded.option_mappings,
        model_id="fake/model",
        requested_model_revision=revision,
        tokenizer_id="fake/tokenizer",
        requested_tokenizer_revision=revision,
        generation_settings=settings or GenerationSettings(max_new_tokens=32, seed=7),
        requested_device="cpu",
        requested_dtype="float32",
        measurement_prompt_version=prompt_version,
        measurement_timing=MEASUREMENT_TIMING,
        token_selection_policy=token_selection_policy,
        backend_implementation={"name": "fake", "version": "v1"},
    )


def test_experiment_hash_ignores_output_mode_and_run_uuid(tmp_path) -> None:
    loaded = loaded_main()
    settings = GenerationSettings(max_new_tokens=32, seed=7)
    first_args = cli.parse_args(
        [
            "--dry-run",
            "--model",
            "fake/model",
            "--revision",
            "revision-1",
            "--output",
            str(tmp_path / "first.jsonl"),
        ]
    )
    second_args = cli.parse_args(
        [
            "--model",
            "fake/model",
            "--revision",
            "revision-1",
            "--output",
            str(tmp_path / "second.jsonl"),
            "--overwrite",
        ]
    )
    stable = cli.build_cli_experiment_configuration(first_args, loaded, settings)
    second_configuration = cli.build_cli_experiment_configuration(
        second_args, loaded, settings
    )
    stable_hash = experiment_configuration_sha256(stable)
    first_plan = cli.planned_configuration(first_args, stable)
    second_plan = cli.planned_configuration(second_args, second_configuration)
    first_metadata = RunMetadata("run-one", "trajectory", stable, stable_hash)
    second_metadata = RunMetadata("run-two", "trajectory", stable, stable_hash)

    assert stable == second_configuration
    assert first_plan["experiment_configuration_sha256"] == stable_hash
    assert second_plan["experiment_configuration_sha256"] == stable_hash
    assert first_metadata.experiment_configuration_sha256 == stable_hash
    assert second_metadata.experiment_configuration_sha256 == stable_hash
    assert first_plan["execution_metadata"] != second_plan["execution_metadata"]
    assert str(ROOT) not in json.dumps(stable)


def test_experiment_hash_changes_for_material_inputs(tmp_path) -> None:
    loaded = loaded_main()
    baseline = configuration(loaded)
    changed_fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    changed_fixture["description"] += " Content hash sensitivity."
    changed_fixture_path = tmp_path / "changed.json"
    changed_fixture_path.write_text(json.dumps(changed_fixture), encoding="utf-8")
    loaded_changed_fixture = load_trajectory(
        dataset_path=DATASET,
        case_id=CASE_ID,
        fixture_path=changed_fixture_path,
    )
    changed_mappings = (
        replace(loaded.option_mappings[0], mapping_id="changed-mapping"),
        *loaded.option_mappings[1:],
    )
    alternatives = [
        configuration(loaded, revision="revision-2"),
        configuration(loaded_changed_fixture),
        configuration(
            loaded, settings=GenerationSettings(max_new_tokens=33, seed=7)
        ),
        configuration(loaded, prompt_version="shadow-prompt-v2"),
        configuration(loaded, token_selection_policy="different-policy-v1"),
        configuration(loaded, mappings=changed_mappings),
    ]

    assert all(
        experiment_configuration_sha256(candidate)
        != experiment_configuration_sha256(baseline)
        for candidate in alternatives
    )


def test_dry_run_never_imports_transformers_or_changes_output(
    tmp_path, monkeypatch, capsys
) -> None:
    output = tmp_path / "existing.jsonl"
    output.write_text("unchanged\n", encoding="utf-8")
    real_import = builtins.__import__

    def guarded_import(name: str, *args, **kwargs):
        if name == "transformers" or name.startswith("transformers."):
            raise AssertionError("dry-run imported Transformers")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = cli.main(
        [
            "--dry-run",
            "--overwrite",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert output.read_text(encoding="utf-8") == "unchanged\n"
    assert "no model was loaded and no event log was written" in capsys.readouterr().out


def test_malformed_fixture_fails_in_dry_run_before_backend_loading(
    tmp_path, monkeypatch
) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["expected_measurement_count"] = 15
    fixture_path = tmp_path / "malformed.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    monkeypatch.setattr(
        cli.HuggingFaceLocalBackend,
        "from_pretrained",
        lambda **kwargs: pytest.fail("backend was loaded"),
    )

    with pytest.raises(ValueError, match="expected_measurement_count"):
        cli.main(
            [
                "--dry-run",
                "--trajectory",
                str(fixture_path),
                "--output",
                str(tmp_path / "unused.jsonl"),
            ]
        )


def test_unknown_case_cli_error_occurs_before_backend_loading(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        cli.HuggingFaceLocalBackend,
        "from_pretrained",
        lambda **kwargs: pytest.fail("backend was loaded"),
    )

    with pytest.raises(ValueError, match="fixture case_id does not match"):
        cli.main(
            [
                "--dry-run",
                "--case-id",
                "missing-case",
                "--output",
                str(tmp_path / "unused.jsonl"),
            ]
        )


def test_tokenizer_check_uses_all_placeholder_contexts_without_output(
    tmp_path, monkeypatch, capsys
) -> None:
    output = tmp_path / "must-not-change.jsonl"
    output.write_text("existing\n", encoding="utf-8")
    tokenizer = TokenizerOnlyFake()
    monkeypatch.setattr(cli, "load_huggingface_tokenizer", lambda *args: tokenizer)
    monkeypatch.setattr(
        cli.HuggingFaceLocalBackend,
        "from_pretrained",
        lambda **kwargs: pytest.fail("model weights were loaded"),
    )

    result = cli.main(
        ["--tokenizer-check", "--output", str(output)]
    )
    captured = capsys.readouterr().out

    assert result == 0
    assert output.read_text(encoding="utf-8") == "existing\n"
    assert captured.count('"checkpoint_index"') == 16
    assert captured.count('"token_id": 65') == 16
    assert captured.count('"token_id": 66') == 16
    assert captured.count('"decoded_text": "A"') == 16
    assert captured.count('"decoded_text": "B"') == 16
    assert '"token_selection_policy": "canonical_exact_label_v1"' in captured
    assert "validates tokenisation only, not model behaviour" in captured


def test_tokenizer_check_contexts_include_marked_assistant_placeholders() -> None:
    loaded = loaded_main()
    tokenizer = TokenizerOnlyFake()

    results = cli.tokenizer_check_contexts(loaded, tokenizer)

    assert len(results) == 16
    for checkpoint in range(4):
        expected = cli.TOKENIZER_CHECK_PLACEHOLDER.format(index=checkpoint)
        assert any(
            any(
                message["role"] == "assistant"
                and message["content"] == expected
                for message in messages
            )
            for messages in tokenizer.calls
        )
