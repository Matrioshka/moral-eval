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
    assert "Open gate: -" in output


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
                "expected_completed_path": "data/generated/run/adjudication_completed.csv",
            },
        },
    )

    assert moral_gen.command_status(argparse.Namespace(run_slug="run_slug")) == 0
    output = capsys.readouterr().out
    assert "Status: running" in output
    assert "Next pending stage: apply_adjudication" in output
    assert "Open gate: adjudication (human_csv_review)" in output
    assert "Expected file: data/generated/run/adjudication_completed.csv" in output

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

    assert moral_gen.command_artifacts(argparse.Namespace(run_slug="run_slug")) == 0
    output = capsys.readouterr().out
    assert "manifest" in output
    assert "experiments/run.yaml" in output
    assert "unchanged" in output


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
