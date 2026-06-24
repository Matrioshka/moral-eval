from __future__ import annotations

import argparse

import scripts.moral_gen as moral_gen


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self):
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return FakeCursor()

    def commit(self):
        self.committed = True


def test_cli_parser_exposes_only_initial_control_commands() -> None:
    parser = moral_gen.build_parser()
    assert parser.parse_args(["init", "run.yaml"]).command == "init"
    assert parser.parse_args(["status", "run_slug"]).command == "status"
    assert parser.parse_args(["artifacts", "run_slug"]).command == "artifacts"
    important = parser.parse_args(["artifacts", "run_slug", "--important"])
    assert important.command == "artifacts"
    assert important.important is True
    assert parser.parse_args(["next", "run_slug"]).command == "next"


def test_cli_help_lists_control_commands(capsys) -> None:
    try:
        moral_gen.build_parser().parse_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "init" in output
    assert "status" in output
    assert "artifacts" in output
    assert "next" in output


def test_status_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(
        moral_gen,
        "read_run_status",
        lambda _cur, _slug: {
            "run": {
                "run_slug": "run_slug",
                "status": "initialised",
                "current_stage": None,
                "last_error": None,
            },
            "next_stage": {"stage_key": "generate_candidates"},
            "open_gate": None,
        },
    )

    assert moral_gen.command_status(argparse.Namespace(run_slug="run_slug")) == 0
    output = capsys.readouterr().out
    assert "Status: initialised" in output
    assert "Next pending stage: generate_candidates" in output
    assert "Gate: -" in output


def test_status_displays_open_gate_without_implying_global_wait(monkeypatch, capsys) -> None:
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(
        moral_gen,
        "read_run_status",
        lambda _cur, _slug: {
            "run": {
                "run_slug": "run_slug",
                "status": "running",
                "current_stage": None,
                "last_error": None,
            },
            "next_stage": {"stage_key": "apply_adjudication"},
            "open_gate": {
                "gate_key": "adjudication",
                "gate_type": "human_csv_review",
                "status": "open",
                "expected_completed_path": "data/generated/run/adjudication_completed.csv",
            },
        },
    )

    assert moral_gen.command_status(argparse.Namespace(run_slug="run_slug")) == 0
    output = capsys.readouterr().out
    assert "Status: running" in output
    assert "Next pending stage: apply_adjudication" in output
    assert "Gate: adjudication (human_csv_review) [open]" in output
    assert "Expected file: data/generated/run/adjudication_completed.csv" in output

def test_status_displays_satisfied_gate(monkeypatch, capsys) -> None:
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(
        moral_gen,
        "read_run_status",
        lambda _cur, _slug: {
            "run": {
                "run_slug": "run_slug",
                "status": "running",
                "current_stage": None,
                "last_error": None,
            },
            "next_stage": {"stage_key": "prepare_revision"},
            "open_gate": None,
            "gate_status": {
                "gate_key": "adjudication",
                "gate_type": "human_csv_review",
                "status": "satisfied",
                "expected_completed_path": "data/generated/run/adjudication_completed.csv",
            },
        },
    )

    assert moral_gen.command_status(argparse.Namespace(run_slug="run_slug")) == 0
    output = capsys.readouterr().out
    assert "Gate: adjudication (human_csv_review) [satisfied]" in output


def test_status_displays_completed_run(monkeypatch, capsys) -> None:
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(
        moral_gen,
        "read_run_status",
        lambda _cur, _slug: {
            "run": {
                "run_slug": "run_slug",
                "status": "completed",
                "current_stage": None,
                "last_error": None,
            },
            "next_stage": None,
            "open_gate": None,
        },
    )

    assert moral_gen.command_status(argparse.Namespace(run_slug="run_slug")) == 0
    output = capsys.readouterr().out
    assert "Status: completed" in output
    assert "Next pending stage: -" in output


def test_artifacts_output(monkeypatch, capsys) -> None:
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(
        moral_gen,
        "list_artifacts",
        lambda _cur, _slug, **_kwargs: [
            {
                "artifact_key": "manifest",
                "artifact_path": "experiments/run.yaml",
                "sha256": "a" * 64,
                "row_count": None,
                "byte_count": 42,
                "human_edited": False,
                "drift": "unchanged",
            }
        ],
    )

    assert moral_gen.command_artifacts(
        argparse.Namespace(run_slug="run_slug", important=False)
    ) == 0
    output = capsys.readouterr().out
    assert "manifest" in output
    assert "experiments/run.yaml" in output
    assert "unchanged" in output


def test_important_artifacts_filter_canonical_and_omits_stale_legacy_templates() -> None:
    status = {
        "run": {"run_slug": "run_slug", "status": "running", "current_stage": None, "last_error": None},
        "next_stage": {"stage_key": "apply_manual_review"},
        "open_gate": {
            "gate_key": "manual_review",
            "gate_type": "human_csv_review",
            "status": "open",
            "expected_completed_path": "data/generated/run/manual_review_completed.csv",
        },
    }
    artifacts = [
        {"artifact_key": "manifest", "artifact_path": "run.yaml"},
        {"artifact_key": "summary", "artifact_path": "summary.json"},
        {"artifact_key": "manual_review_template_csv", "artifact_path": "manual_review_template.csv"},
        {"artifact_key": "manual_review_template_jsonl", "artifact_path": "manual_review_template.jsonl"},
        {"artifact_key": "manual_review_gate_template", "artifact_path": "manual_review_gate_template.csv"},
        {"artifact_key": "manual_review_completed", "artifact_path": "manual_review_completed.csv", "human_edited": True},
        {"artifact_key": "reviewed_candidates", "artifact_path": "reviewed_candidates.jsonl"},
        {"artifact_key": "raw_candidates", "artifact_path": "raw_candidates.jsonl", "row_count": 12},
    ]

    keys = [item["artifact_key"] for item in moral_gen.important_artifacts(artifacts, status)]

    assert keys == [
        "manifest",
        "summary",
        "manual_review_gate_template",
        "manual_review_completed",
        "reviewed_candidates",
    ]


def test_important_counts_uses_recorded_rows() -> None:
    counts = moral_gen.important_counts(
        [
            {"artifact_key": "raw_candidates", "row_count": 12},
            {"artifact_key": "scored_candidates", "row_count": 12},
            {"artifact_key": "kept_candidates", "row_count": 2},
            {"artifact_key": "reviewed_candidates", "row_count": 1},
            {"artifact_key": "inspect_dataset", "row_count": 1},
            {"artifact_key": "manual_review_template_csv", "row_count": 99},
        ]
    )

    assert counts == [
        ("generated", 12),
        ("scored", 12),
        ("kept", 2),
        ("reviewed", 1),
        ("exported inspect items", 1),
    ]


def test_important_artifacts_output_includes_status_counts_and_filtered_table(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: FakeConnection())
    monkeypatch.setattr(
        moral_gen,
        "read_run_status",
        lambda _cur, _slug: {
            "run": {
                "run_slug": "run_slug",
                "status": "completed",
                "current_stage": None,
                "last_error": None,
            },
            "next_stage": None,
            "open_gate": None,
        },
    )
    monkeypatch.setattr(
        moral_gen,
        "list_artifacts",
        lambda _cur, _slug, **_kwargs: [
            {
                "artifact_key": "manifest",
                "artifact_path": "experiments/run.yaml",
                "sha256": "a" * 64,
                "row_count": None,
                "byte_count": 42,
                "human_edited": False,
                "drift": "unchanged",
            },
            {
                "artifact_key": "raw_candidates",
                "artifact_path": "raw_candidates.jsonl",
                "sha256": "b" * 64,
                "row_count": 12,
                "byte_count": 120,
                "human_edited": False,
                "drift": "unchanged",
            },
            {
                "artifact_key": "manual_review_template_csv",
                "artifact_path": "manual_review_template.csv",
                "sha256": "c" * 64,
                "row_count": 1,
                "byte_count": 120,
                "human_edited": False,
                "drift": "unchanged",
            },
            {
                "artifact_key": "inspect_dataset",
                "artifact_path": "phase3.jsonl",
                "sha256": "d" * 64,
                "row_count": 1,
                "byte_count": 100,
                "human_edited": False,
                "drift": "unchanged",
            },
        ],
    )

    assert moral_gen.command_artifacts(
        argparse.Namespace(run_slug="run_slug", important=True)
    ) == 0
    output = capsys.readouterr().out
    assert "Status: completed" in output
    assert "generated: 12" in output
    assert "exported inspect items: 1" in output
    assert "manifest" in output
    assert "inspect_dataset" in output
    assert "manual_review_template_csv" not in output


def test_init_output_and_transaction(monkeypatch, capsys) -> None:
    connection = FakeConnection()
    loaded = type("Loaded", (), {"planned_stages": ["generate_candidates"]})()
    monkeypatch.setattr(moral_gen, "load_manifest", lambda *_args, **_kwargs: loaded)
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: connection)
    monkeypatch.setattr(moral_gen, "apply_migration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        moral_gen,
        "initialise_run",
        lambda _cur, _loaded: (
            {
                "run_slug": "run_slug",
                "status": "initialised",
                "output_dir": "data/generated/run_slug",
                "manifest_sha256": "abc",
            },
            True,
        ),
    )

    assert moral_gen.command_init(argparse.Namespace(manifest="run.yaml")) == 0
    assert connection.committed is True
    output = capsys.readouterr().out
    assert "Initialised: run_slug" in output
    assert "Planned stages: 1" in output


def test_main_reports_control_errors(monkeypatch, capsys) -> None:
    monkeypatch.setitem(
        moral_gen.command_status.__globals__,
        "read_run_status",
        lambda _cur, _slug: (_ for _ in ()).throw(moral_gen.RunNotFoundError("missing")),
    )
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: FakeConnection())
    assert moral_gen.main(["status", "missing"]) == 2
    assert "Error: missing" in capsys.readouterr().err

def test_next_output(monkeypatch, capsys) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(moral_gen, "connect_db", lambda **_kwargs: connection)
    monkeypatch.setattr(
        moral_gen,
        "advance_one",
        lambda _connection, slug, **_kwargs: type(
            "Result", (), {"message": f"Completed stage for {slug}."}
        )(),
    )

    assert moral_gen.command_next(argparse.Namespace(run_slug="run_slug")) == 0
    assert "Completed stage for run_slug." in capsys.readouterr().out
