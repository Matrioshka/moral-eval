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
            "output_dir": "data/generated/runner_test",
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
        "mark_stage_skipped",
        lambda _cur, **kwargs: events.append(("skipped", kwargs["stage_id"])),
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
    monkeypatch.setattr(
        runner,
        "satisfy_adjudication_gate",
        lambda _cur, **kwargs: events.append(
            ("gate_satisfied", kwargs["completed_artifact_id"])
        ),
    )
    monkeypatch.setattr(
        runner,
        "open_revision_gate",
        lambda _cur, **kwargs: events.append(
            ("revision_gate", kwargs["expected_completed_path"])
        ),
    )
    monkeypatch.setattr(
        runner,
        "satisfy_revision_gate",
        lambda _cur, **kwargs: events.append(
            ("revision_gate_satisfied", kwargs["completed_artifact_id"])
        ),
    )
    monkeypatch.setattr(
        runner,
        "open_revised_adjudication_gate",
        lambda _cur, **kwargs: events.append(
            ("revised_adjudication_gate", kwargs["expected_completed_path"])
        ),
    )
    monkeypatch.setattr(
        runner,
        "satisfy_revised_adjudication_gate",
        lambda _cur, **kwargs: events.append(
            ("revised_adjudication_gate_satisfied", kwargs["completed_artifact_id"])
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


def test_apply_adjudication_records_artifacts_satisfies_gate_and_completes_once(
    tmp_path: Path, monkeypatch
) -> None:
    gate = {
        "dataset_generation_gate_id": 40,
        "gate_key": "adjudication",
        "template_path": "data/generated/runner_test/adjudication_template.csv",
        "expected_completed_path": "data/generated/runner_test/adjudication_completed.csv",
        "instructions": "Complete the CSV.",
    }
    completed = tmp_path / gate["expected_completed_path"]
    completed.parent.mkdir(parents=True, exist_ok=True)
    completed.write_text("case_id,overall_verdict\ncase-1,keep\n", encoding="utf-8")
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("apply_adjudication", gate=gate), events)

    def apply(manifest, repo_root: Path, completed_csv: Path):
        assert completed_csv == completed
        output_dir = repo_root / manifest.run.output_dir
        output_jsonl = output_dir / "adjudicated_candidates.jsonl"
        summary_json = output_dir / "adjudication_summary.json"
        output_jsonl.write_text('{"case_id":"case-1"}\n', encoding="utf-8")
        summary_json.write_text('{"total":1}\n', encoding="utf-8")
        return output_jsonl, summary_json, 1

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        apply_adjudication_executor=apply,
    )

    assert result.outcome == "completed"
    assert result.stage_key == "apply_adjudication"
    assert [event[1] for event in events if event[0] == "artifact"] == [
        "adjudication_completed",
        "adjudicated_candidates",
        "adjudication_summary",
    ]
    assert ("gate_satisfied", 30) in events
    assert events.count(("completed", 20)) == 1
    assert not any(event[0] == "gate" for event in events)


def test_invalid_completed_adjudication_fails_stage_and_leaves_gate_open(
    tmp_path: Path, monkeypatch
) -> None:
    gate = {
        "dataset_generation_gate_id": 40,
        "gate_key": "adjudication",
        "template_path": "data/generated/runner_test/adjudication_template.csv",
        "expected_completed_path": "data/generated/runner_test/adjudication_completed.csv",
        "instructions": "Complete the CSV.",
    }
    completed = tmp_path / gate["expected_completed_path"]
    completed.parent.mkdir(parents=True, exist_ok=True)
    completed.write_text("invalid,data\n", encoding="utf-8")
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("apply_adjudication", gate=gate), events)

    def invalid(_manifest, _repo_root: Path, _completed_csv: Path):
        raise ValueError("Adjudication CSV is missing required columns")

    with pytest.raises(StageExecutionError, match="missing required columns"):
        advance_one(
            FakeConnection(),
            "runner_test",
            repo_root=tmp_path,
            apply_adjudication_executor=invalid,
        )

    assert any(
        event[0] == "failed" and "missing required columns" in event[1]
        for event in events
    )
    assert not any(event[0] == "gate_satisfied" for event in events)
    assert not any(event[0] == "completed" for event in events)

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

def revision_gate() -> dict:
    return {
        "dataset_generation_gate_id": 50,
        "gate_key": "revision",
        "template_path": "data/generated/runner_test/revision_notes.csv",
        "expected_completed_path": "data/generated/runner_test/revision_notes_completed.csv",
        "instructions": "Complete the revision worksheet.",
    }


def test_prepare_revision_records_only_revision_outputs_and_opens_gate(
    tmp_path: Path, monkeypatch
) -> None:
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("prepare_revision"), events)

    def prepare(manifest, repo_root: Path):
        output = repo_root / manifest.run.output_dir
        output.mkdir(parents=True, exist_ok=True)
        revise = output / "revise_candidates.jsonl"
        notes = output / "revision_notes.csv"
        revise.write_text('{"case_id":"revise-1"}\n', encoding="utf-8")
        notes.write_text("case_id,revision_notes\nrevise-1,fix it\n", encoding="utf-8")
        return revise, notes, 1

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        prepare_revision_executor=prepare,
    )

    assert result.outcome == "completed"
    assert result.stage_key == "prepare_revision"
    assert [event[1] for event in events if event[0] == "artifact"] == [
        "revise_candidates",
        "revision_notes",
    ]
    assert ("revision_gate", "data/generated/runner_test/revision_notes_completed.csv") in events
    assert ("completed", 20) in events
    assert "gate B" in result.message


def test_prepare_completes_and_apply_skips_when_no_revision_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    prepare_events: list[tuple] = []
    patch_db(monkeypatch, execution_state("prepare_revision"), prepare_events)

    def prepare_empty(manifest, repo_root: Path):
        output = repo_root / manifest.run.output_dir
        output.mkdir(parents=True, exist_ok=True)
        revise = output / "revise_candidates.jsonl"
        notes = output / "revision_notes.csv"
        revise.write_text("", encoding="utf-8")
        notes.write_text("case_id,revision_notes\n", encoding="utf-8")
        return revise, notes, 0

    prepare_result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        prepare_revision_executor=prepare_empty,
    )
    assert prepare_result.outcome == "completed"
    assert ("completed", 20) in prepare_events
    assert not any(event[0] == "revision_gate" for event in prepare_events)

    apply_events: list[tuple] = []
    patch_db(monkeypatch, execution_state("apply_revision"), apply_events)
    apply_result = advance_one(FakeConnection(), "runner_test", repo_root=tmp_path)
    assert apply_result.outcome == "skipped"
    assert apply_events == [
        ("lock", "runner_test"),
        ("skipped", 20),
        ("unlock", "runner_test"),
    ]


def test_apply_revision_waits_for_completed_notes(tmp_path: Path, monkeypatch) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revise_candidates.jsonl").write_text(
        '{"case_id":"revise-1"}\n', encoding="utf-8"
    )
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("apply_revision", gate=revision_gate()), events)

    result = advance_one(FakeConnection(), "runner_test", repo_root=tmp_path)

    assert result.outcome == "stage_waiting_human"
    assert result.stage_key == "apply_revision"
    assert "revision_notes_completed.csv" in result.message
    assert events == [("lock", "runner_test"), ("unlock", "runner_test")]


def test_apply_revision_records_artifacts_satisfies_gate_and_completes_once(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revise_candidates.jsonl").write_text(
        '{"case_id":"revise-1"}\n', encoding="utf-8"
    )
    completed = output / "revision_notes_completed.csv"
    completed.write_text("case_id,revision_notes\nrevise-1,fixed\n", encoding="utf-8")
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("apply_revision", gate=revision_gate()), events)

    def apply(manifest, repo_root: Path, completed_csv: Path):
        assert completed_csv == completed
        revised = repo_root / manifest.run.output_dir / "revised_candidates.jsonl"
        revised.write_text('{"case_id":"revise-1"}\n', encoding="utf-8")
        return revised, 1

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        apply_revision_executor=apply,
    )

    assert result.outcome == "completed"
    assert [event[1] for event in events if event[0] == "artifact"] == [
        "revision_notes_completed",
        "revised_candidates",
    ]
    assert ("revision_gate_satisfied", 30) in events
    assert events.count(("completed", 20)) == 1
    assert "await re-adjudication" in result.message


def test_invalid_revision_notes_fail_stage_and_leave_gate_open(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revise_candidates.jsonl").write_text(
        '{"case_id":"revise-1"}\n', encoding="utf-8"
    )
    (output / "revision_notes_completed.csv").write_text(
        "invalid,data\n", encoding="utf-8"
    )
    events: list[tuple] = []
    patch_db(monkeypatch, execution_state("apply_revision", gate=revision_gate()), events)

    def invalid(_manifest, _repo_root: Path, _completed_csv: Path):
        raise ValueError("Revision-notes CSV is missing required columns")

    with pytest.raises(StageExecutionError, match="missing required columns"):
        advance_one(
            FakeConnection(),
            "runner_test",
            repo_root=tmp_path,
            apply_revision_executor=invalid,
        )

    assert any(event[0] == "failed" for event in events)
    assert not any(event[0] == "revision_gate_satisfied" for event in events)
    assert not any(event[0] == "completed" for event in events)


@pytest.mark.parametrize("stage_key", ["prepare_revision", "apply_revision"])
def test_revision_executors_refuse_conflicting_outputs(
    tmp_path: Path, stage_key: str
) -> None:
    manifest = runner.manifest_from_snapshot(manifest_snapshot(allow_model_calls=False))
    output = tmp_path / manifest.run.output_dir
    output.mkdir(parents=True)
    if stage_key == "prepare_revision":
        (output / "revision_notes.csv").write_text("existing", encoding="utf-8")
        with pytest.raises(FileExistsError, match="refusing to overwrite revision output"):
            runner.execute_prepare_revision(manifest, tmp_path)
    else:
        (output / "revised_candidates.jsonl").write_text("existing", encoding="utf-8")
        with pytest.raises(FileExistsError, match="refusing to overwrite revision output"):
            runner.execute_apply_revision(
                manifest, tmp_path, output / "revision_notes_completed.csv"
            )

def revised_adjudication_gate() -> dict:
    return {
        "dataset_generation_gate_id": 60,
        "gate_key": "revised_adjudication",
        "template_path": "data/generated/runner_test/revised_adjudication_template.csv",
        "expected_completed_path": "data/generated/runner_test/revised_adjudication_completed.csv",
        "instructions": "Complete revised adjudication.",
    }


def test_revised_adjudication_stages_skip_when_no_revised_candidates(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revise_candidates.jsonl").write_text("", encoding="utf-8")

    for stage_key in (
        "prepare_revised_adjudication",
        "apply_revised_adjudication",
    ):
        events: list[tuple] = []
        patch_db(monkeypatch, execution_state(stage_key), events)
        result = advance_one(FakeConnection(), "runner_test", repo_root=tmp_path)
        assert result.outcome == "skipped"
        assert ("skipped", 20) in events
        assert not any(
            event[0] == "revised_adjudication_gate" for event in events
        )


def test_prepare_revised_adjudication_records_template_and_opens_dedicated_gate(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revised_candidates.jsonl").write_text(
        '{"case_id":"revised-1"}\n', encoding="utf-8"
    )
    events: list[tuple] = []
    patch_db(
        monkeypatch,
        execution_state("prepare_revised_adjudication"),
        events,
    )

    def prepare(manifest, repo_root: Path):
        template = (
            repo_root
            / manifest.run.output_dir
            / "revised_adjudication_template.csv"
        )
        template.write_text("case_id\nrevised-1\n", encoding="utf-8")
        return template

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        revised_adjudication_executor=prepare,
    )
    assert result.outcome == "completed"
    assert ("artifact", "revised_adjudication_template") in events
    assert (
        "revised_adjudication_gate",
        "data/generated/runner_test/revised_adjudication_completed.csv",
    ) in events
    assert not any(event[0] == "gate" for event in events)


def test_apply_revised_adjudication_waits_for_its_completed_csv(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revised_candidates.jsonl").write_text(
        '{"case_id":"revised-1"}\n', encoding="utf-8"
    )
    events: list[tuple] = []
    patch_db(
        monkeypatch,
        execution_state(
            "apply_revised_adjudication",
            gate=revised_adjudication_gate(),
        ),
        events,
    )
    result = advance_one(FakeConnection(), "runner_test", repo_root=tmp_path)
    assert result.outcome == "stage_waiting_human"
    assert "revised_adjudication_completed.csv" in result.message
    assert events == [("lock", "runner_test"), ("unlock", "runner_test")]


def test_apply_revised_adjudication_records_distinct_outputs_and_satisfies_gate(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revised_candidates.jsonl").write_text(
        '{"case_id":"revised-1"}\n', encoding="utf-8"
    )
    completed = output / "revised_adjudication_completed.csv"
    completed.write_text("case_id\nrevised-1\n", encoding="utf-8")
    original_summary = output / "adjudication_summary.json"
    original_summary.write_text('{"original":true}\n', encoding="utf-8")
    events: list[tuple] = []
    patch_db(
        monkeypatch,
        execution_state(
            "apply_revised_adjudication",
            gate=revised_adjudication_gate(),
        ),
        events,
    )

    def apply(manifest, repo_root: Path, completed_csv: Path):
        assert completed_csv == completed
        target = repo_root / manifest.run.output_dir
        output_jsonl = target / "adjudicated_revised_candidates.jsonl"
        summary = target / "revised_adjudication_summary.json"
        output_jsonl.write_text('{"case_id":"revised-1"}\n', encoding="utf-8")
        summary.write_text('{"total":1}\n', encoding="utf-8")
        return output_jsonl, summary, 1

    result = advance_one(
        FakeConnection(),
        "runner_test",
        repo_root=tmp_path,
        apply_revised_adjudication_executor=apply,
    )
    assert result.outcome == "completed"
    assert [event[1] for event in events if event[0] == "artifact"] == [
        "revised_adjudication_completed",
        "adjudicated_revised_candidates",
        "revised_adjudication_summary",
    ]
    assert ("revised_adjudication_gate_satisfied", 30) in events
    assert original_summary.read_text(encoding="utf-8") == '{"original":true}\n'
    assert "not eligible for manual review or export" in result.message


def test_invalid_revised_adjudication_fails_stage_and_leaves_gate_open(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "data/generated/runner_test"
    output.mkdir(parents=True)
    (output / "revised_candidates.jsonl").write_text(
        '{"case_id":"revised-1"}\n', encoding="utf-8"
    )
    (output / "revised_adjudication_completed.csv").write_text(
        "invalid,data\n", encoding="utf-8"
    )
    events: list[tuple] = []
    patch_db(
        monkeypatch,
        execution_state(
            "apply_revised_adjudication",
            gate=revised_adjudication_gate(),
        ),
        events,
    )

    def invalid(_manifest, _root: Path, _completed: Path):
        raise ValueError("Adjudication CSV is missing required columns")

    with pytest.raises(StageExecutionError, match="missing required columns"):
        advance_one(
            FakeConnection(),
            "runner_test",
            repo_root=tmp_path,
            apply_revised_adjudication_executor=invalid,
        )
    assert any(event[0] == "failed" for event in events)
    assert not any(
        event[0] == "revised_adjudication_gate_satisfied" for event in events
    )


@pytest.mark.parametrize(
    "stage_key",
    ["prepare_revised_adjudication", "apply_revised_adjudication"],
)
def test_revised_adjudication_executors_refuse_conflicting_outputs(
    tmp_path: Path, stage_key: str
) -> None:
    manifest = runner.manifest_from_snapshot(
        manifest_snapshot(allow_model_calls=False)
    )
    output = tmp_path / manifest.run.output_dir
    output.mkdir(parents=True)
    if stage_key == "prepare_revised_adjudication":
        (output / "revised_adjudication_template.csv").write_text(
            "existing", encoding="utf-8"
        )
        with pytest.raises(FileExistsError, match="refusing to overwrite"):
            runner.execute_prepare_revised_adjudication(manifest, tmp_path)
    else:
        (output / "adjudicated_revised_candidates.jsonl").write_text(
            "existing", encoding="utf-8"
        )
        with pytest.raises(FileExistsError, match="refusing to overwrite"):
            runner.execute_apply_revised_adjudication(
                manifest,
                tmp_path,
                output / "revised_adjudication_completed.csv",
            )
