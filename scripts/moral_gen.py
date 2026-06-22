#!/usr/bin/env python
"""Minimal DB-backed control CLI for dataset-generation manifests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from moral_eval.dataset_generation.control_db import (  # noqa: E402
    ManifestConflictError,
    RunNotFoundError,
    apply_migration,
    connect_db,
    initialise_run,
    list_artifacts,
    read_run_status,
)
from moral_eval.dataset_generation.manifest import load_manifest  # noqa: E402
from moral_eval.dataset_generation.runner import advance_one  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control and advance manifest-driven dataset-generation runs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Validate and register a manifest.")
    init_parser.add_argument("manifest", type=Path)

    status_parser = subparsers.add_parser("status", help="Show one run's control status.")
    status_parser.add_argument("run_slug")

    artifacts_parser = subparsers.add_parser(
        "artifacts", help="List recorded artefacts and filesystem drift."
    )
    artifacts_parser.add_argument("run_slug")

    next_parser = subparsers.add_parser(
        "next", help="Advance at most one stage, stopping at an open human gate."
    )
    next_parser.add_argument("run_slug")
    return parser


def command_init(args: argparse.Namespace) -> int:
    loaded = load_manifest(args.manifest, repo_root=ROOT)
    with connect_db(repo_root=ROOT) as connection, connection.cursor() as cur:
        apply_migration(cur, repo_root=ROOT)
        run, created = initialise_run(cur, loaded)
        connection.commit()
    action = "Initialised" if created else "Already initialised"
    print(f"{action}: {run['run_slug']}")
    print(f"Status: {run['status']}")
    print(f"Output directory: {run['output_dir']}")
    print(f"Manifest SHA-256: {run['manifest_sha256']}")
    print(f"Planned stages: {len(loaded.planned_stages)}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    with connect_db(repo_root=ROOT) as connection, connection.cursor() as cur:
        status = read_run_status(cur, args.run_slug)
    run = status["run"]
    print(f"Run: {run['run_slug']}")
    print(f"Status: {run['status']}")
    print(f"Current stage: {run.get('current_stage') or '-'}")
    next_stage = status["next_stage"]
    print(f"Next pending stage: {next_stage['stage_key'] if next_stage else '-'}")
    gate = status.get("gate_status") or status.get("open_gate")
    if gate:
        print(
            f"Gate: {gate['gate_key']} ({gate['gate_type']}) "
            f"[{gate['status']}]"
        )
        if gate.get("expected_completed_path"):
            print(f"Expected file: {gate['expected_completed_path']}")
    else:
        print("Gate: -")
    print(f"Last error: {run.get('last_error') or '-'}")
    return 0


def command_artifacts(args: argparse.Namespace) -> int:
    with connect_db(repo_root=ROOT) as connection, connection.cursor() as cur:
        artifacts = list_artifacts(cur, args.run_slug, repo_root=ROOT)
    if not artifacts:
        print(f"No artefacts recorded for {args.run_slug}.")
        return 0

    headers = ("KEY", "PATH", "HASH", "ROWS", "BYTES", "HUMAN", "DRIFT")
    rows = [
        (
            str(item["artifact_key"]),
            str(item["artifact_path"]),
            str(item.get("sha256") or "-")[:12],
            str(item["row_count"]) if item.get("row_count") is not None else "-",
            str(item["byte_count"]) if item.get("byte_count") is not None else "-",
            "yes" if item.get("human_edited") else "no",
            str(item["drift"]),
        )
        for item in artifacts
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print("  ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    return 0


def command_next(args: argparse.Namespace) -> int:
    with connect_db(repo_root=ROOT) as connection:
        result = advance_one(connection, args.run_slug, repo_root=ROOT)
    print(result.message)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "init": command_init,
        "status": command_status,
        "artifacts": command_artifacts,
        "next": command_next,
    }
    try:
        return commands[args.command](args)
    except (ManifestConflictError, RunNotFoundError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
