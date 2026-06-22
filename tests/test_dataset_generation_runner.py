from __future__ import annotations

from pathlib import Path

import pytest

from moral_eval.dataset_generation import runner
from moral_eval.dataset_generation.control_db import RunLockUnavailableError
from moral_eval.dataset_generation.runner import StageExecutionError, advance_one


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.cursor_value = FakeCursor()

    def cursor(self):
        return self.cursor_value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def manifest_snapshot(*, allow_model_calls: bool) -> dict:
    return {
        "schema_version": "dataset_generation_control_v1",
        "run": {
            "slug": "runner_test",
            "name": "Runner test",
            "output_dir": "data/generated/runner_test",
        },
        "cells": {"file": "cells.jsonl", "limit": 1},
        "generation": {
            "mode": "single_pass",
            "provider": "openai-parse",
            "base_url": None,
            "generator_model": "generator",
            "judge_model": "judge",
            "seed": 1,
            "n_per_cell": 1,
            "max_workers": 1,
            "target_kept": None,
            "max_batches": None,
        },
        "quality": {
            "min_mean_quality": 8.0,
            "max_duplicate_risk": 4,
            "near_duplicate_threshold": 0.86,
            "allow_validation_errors": False,
        },
        "workflow": {
            "adjudication": True,
            "revisions": False,
            "manual_review": False,
        },
        "safety": {"allow_model_calls": allow_model_calls},
        "export": {
            "inspect_jsonl": False,
            "dataset_version": None,
            "output_path": None,
            "allow_reviewed_validation_errors": False,
        },
        "manifest_path": "experiments/runner_test.yaml",
    }


def execution_state(stage_key: str, *, allow_model_calls: bool = True, gate=None) -> dict:
    return {
        "run": {
            "dataset_generation_run_id": 10,
            "run_slug": "runner_test",
            "status": "initialised",
            "current_stage": None,
            "last_error": None,
            "manifest_snapshot": manifest_snapshot(allow_model_calls=allow_model_calls),
        },
        "next_stage": {
            "dataset_generation_stage_id": 20,
            "stage_key": stage_key,
            "status": "pending",
        },
        "open_gate": gate,
    }


def patch_db(monkeypatch, state: dict, events: list[tuple]) -> None:
    monkeypatch.setattr(runner, "load_run_execution_state", lambda _cur, _slug: state)
    monkeypatch.setattr(
        runner, "acquire_run_lock", lambda _cur, slug: events.append(("lock", slug))
    )
    monkeypatch.setattr(
        runner, "release_run_lock", lambda _cur, slug: events.append(("unlock", slug))
    )
    monkeypatch.setattr(
        runner,
        "mark_stage_running",
        lambda _cur, **kwargs: events.append(("running", kwargs["stage_key"])),
    )
    monkeypatch.setattr(
        runner,
        "mark_stage_completed",
        lambda _cur, **kwargs: events.append(("completed", kwargs["stage_id"])),
    )
    monkeypatch.setattr(
        runner,
        "mark_stage_failed",
        lambda _cur, **kwargs: events.append(("failed", kwargs["error"])),
    )
    monkeypatch.setattr(
        runner,
        "record_artifact",
        lambda _cur, **kwargs: events.append(("artifact", kwargs["artifact_key"])),
    )
    monkeypatch.setattr(runner, "artifact_id", lambda _cur, **_kwargs: 30)
    monkeypatch.setattr(
        runner,
        "open_adjudication_gate",
        lambda _cur, **kwargs: events.append(
            ("gate", kwargs["expected_completed_path"])
        ),
    )


def write_generation_outputs(manifest, repo_root: Path) -> None:
    output_dir = repo_root / manifest.run.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    for _key, filename, artifact_type, _role in runner.GENERATION_ARTIFACTS:
        path = output_dir / filename
        if artifact_type == "csv":
            path.write_text("id\n1\n", encoding="utf-8")
        elif artifact_type == "jsonl":
            path.write_text('{"id":1}\n', encoding="utf-8")
        else:
            path.write_text("{}\n", encoding="utf-8")


def test_next_refuses_generation_without_manifest_opt_in(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("generate_candidates", allow_model_calls=False), events)
    called = False

    def should_not_run(_manifest, _root):
        nonlocal called
        called = True

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        generation_executor=should_not_run,
    )

    assert result.outcome == "model_calls_disabled"
    assert called is False
    assert events == [("lock", "runner_test"), ("unlock", "runner_test")]


def test_next_fails_clearly_when_advisory_lock_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        runner,
        "acquire_run_lock",
        lambda _cur, _slug: (_ for _ in ()).throw(RunLockUnavailableError("locked")),
    )
    released = False

    def release(_cur, _slug):
        nonlocal released
        released = True

    monkeypatch.setattr(runner, "release_run_lock", release)
    with pytest.raises(RunLockUnavailableError, match="locked"):
        advance_one(FakeConnection(), "runner_test", repo_root=tmp_path)
    assert released is False


def test_generate_candidates_records_outputs_and_only_completes_one_stage(
    tmp_path: Path, monkeypatch
) -> None:
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("generate_candidates"), events)

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        generation_executor=write_generation_outputs,
    )

    assert result.outcome == "completed"
    assert result.stage_key == "generate_candidates"
    assert ("running", "generate_candidates") in events
    assert ("completed", 20) in events
    assert [event[1] for event in events if event[0] == "artifact"] == [
        item[0] for item in runner.GENERATION_ARTIFACTS
    ]
    assert not any(event[0] == "gate" for event in events)


def test_prepare_adjudication_opens_gate_without_waiting_run_status(
    tmp_path: Path, monkeypatch
) -> None:
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("prepare_adjudication"), events)

    def prepare(manifest, repo_root: Path) -> Path:
        path = repo_root / manifest.run.output_dir / "adjudication_template.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("case_id,decision\n", encoding="utf-8")
        return path

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        adjudication_executor=prepare,
    )

    assert result.outcome == "completed"
    assert result.template_path == "data/generated/runner_test/adjudication_template.csv"
    assert result.completed_path == "data/generated/runner_test/adjudication_completed.csv"
    assert ("artifact", "adjudication_template") in events
    assert ("gate", result.completed_path) in events
    assert "CSV to edit:" in result.message
    assert "python scripts/moral_gen.py next runner_test" in result.message


def test_next_blocks_only_stage_that_requires_open_gate_file(
    tmp_path: Path, monkeypatch
) -> None:
    gate = {
        "gate_key": "adjudication",
        "template_path": "data/generated/runner_test/adjudication_template.csv",
        "expected_completed_path": "data/generated/runner_test/adjudication_completed.csv",
        "instructions": "Complete the CSV.",
    }
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("apply_adjudication", gate=gate), events)

    result = advance_one(FakeConnection(), "runner_test", repo_root=tmp_path)

    assert result.outcome == "stage_waiting_human"
    assert result.stage_key == "apply_adjudication"
    assert "Required file: data/generated/runner_test/adjudication_completed.csv" in result.message
    assert events == [("lock", "runner_test"), ("unlock", "runner_test")]


def test_open_gate_does_not_block_an_independent_runnable_stage(
    tmp_path: Path, monkeypatch
) -> None:
    gate = {
        "gate_key": "adjudication",
        "template_path": "data/generated/runner_test/adjudication_template.csv",
        "expected_completed_path": "data/generated/runner_test/adjudication_completed.csv",
        "instructions": "Complete the CSV.",
    }
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("generate_candidates", gate=gate), events)

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        generation_executor=write_generation_outputs,
    )

    assert result.outcome == "completed"
    assert ("running", "generate_candidates") in events
    assert ("completed", 20) in events


def test_failed_stage_is_recorded_and_does_not_advance(tmp_path: Path, monkeypatch) -> None:
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("generate_candidates"), events)
    connection = FakeConnection()

    def fail(_manifest, _root):
        raise ValueError("synthetic generation failure")

    with pytest.raises(StageExecutionError, match="synthetic generation failure"):
        advance_one(
            connection,
            "runner_test",
            repo_root=tmp_path,
            generation_executor=fail,
        )

    assert connection.rollbacks == 1
    assert any(event[0] == "failed" for event in events)
    assert not any(event[0] == "completed" for event in events)
    assert not any(event[0] == "gate" for event in events)

def test_failed_run_does_not_skip_to_a_later_pending_stage(tmp_path: Path, monkeypatch) -> None:
    state = execution_state("prepare_adjudication")
    state["run"].update({"status": "failed", "last_error": "generation failed"})
    events: list[tuple] = []
    patch_db(monkeypatch, state, events)

    result = advance_one(FakeConnection(), "runner_test", repo_root=tmp_path)

    assert result.outcome == "failed"
    assert "generation failed" in result.message
    assert events == [("lock", "runner_test"), ("unlock", "runner_test")]
