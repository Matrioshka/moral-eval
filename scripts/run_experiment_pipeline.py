#!/usr/bin/env python
"""Thin local experiment pipeline around the existing Inspect/export/ingest scripts."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - local environment guard
    raise SystemExit("Install dependency first: python -m pip install pyyaml") from exc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = Path("logs")


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding the current shell."""

    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_experiment(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Experiment file must contain a mapping: {path}")
    return data


def require(config: dict[str, Any], key: str) -> str:
    value = config.get(key)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Experiment config is missing required field: {key}")
    return str(value)


def cli_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def build_inspect_command(config: dict[str, Any]) -> list[str]:
    cmd = [
        "inspect",
        "eval",
        require(config, "task"),
        "--model",
        require(config, "model"),
        "-T",
        f"dataset_version={require(config, 'dataset_version')}",
    ]

    for key, value in (config.get("model_args") or {}).items():
        cmd.extend(["-M", f"{key}={cli_value(value)}"])

    limit = config.get("limit")
    if limit not in (None, "", 0, "0"):
        cmd.extend(["--limit", str(limit)])

    return cmd


def run_step(cmd: list[str], root: Path, output_dir: Path, label: str) -> dict[str, Any]:
    proc = subprocess.run(
        cmd,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    stdout_path = output_dir / f"{label}.stdout.txt"
    stderr_path = output_dir / f"{label}.stderr.txt"
    stdout_path.write_text(proc.stdout, encoding="utf-8")
    stderr_path.write_text(proc.stderr, encoding="utf-8")

    step = {
        "label": label,
        "command": shlex.join(cmd),
        "returncode": proc.returncode,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed with exit code {proc.returncode}: {shlex.join(cmd)}")
    return step


def locate_eval_log(root: Path, log_dir: Path, started_at: float) -> Path:
    full_log_dir = log_dir if log_dir.is_absolute() else root / log_dir
    logs = sorted(
        full_log_dir.glob("*.eval"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not logs:
        raise FileNotFoundError(f"No .eval logs found in {full_log_dir}")

    fresh_logs = [path for path in logs if path.stat().st_mtime >= started_at - 5]
    return fresh_logs[0] if fresh_logs else logs[0]


def default_outputs_csv(name: str) -> Path:
    # Keep this one level under tmp/ so the existing ingest allowlist picks it up.
    return Path("tmp") / f"{name}_export.csv"


def export_outputs(log_path: Path, outputs_csv: Path) -> list[str]:
    return [
        sys.executable,
        "src/moral_sycophancy_eval/export_behaviour_outputs.py",
        str(log_path),
        "--csv",
        str(outputs_csv),
    ]


def ingest_command(config: dict[str, Any]) -> list[str] | None:
    ingest = bool(config.get("ingest", True))
    rebuild = bool(config.get("rebuild_derived", True))
    if not ingest and not rebuild:
        return None

    cmd = [sys.executable, "scripts/ingest_eval_artifacts_to_postgres.py"]
    if rebuild:
        cmd.append("--rebuild-derived")
    if not ingest:
        cmd.append("--no-ingest")
    return cmd


def connect_db():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - local environment guard
        raise SystemExit('Install dependency first: python -m pip install "psycopg[binary]"') from exc

    from postgres_schema_config import apply_search_path

    dsn = os.getenv("MORAL_EVALS_DATABASE_URL")
    if dsn:
        conn = psycopg.connect(dsn, row_factory=dict_row)
        apply_search_path(conn)
        return conn

    params = {
        "host": os.getenv("PGHOST") or os.getenv("PG_HOST"),
        "port": os.getenv("PGPORT") or os.getenv("PG_PORT"),
        "dbname": os.getenv("PGDATABASE") or os.getenv("PG_DATABASE"),
        "user": os.getenv("PGUSER") or os.getenv("PG_USER"),
        "password": os.getenv("PGPASSWORD") or os.getenv("PG_PASSWORD"),
    }
    params = {key: value for key, value in params.items() if value}
    if not params:
        raise RuntimeError(
            "No PostgreSQL connection configured. Set MORAL_EVALS_DATABASE_URL, "
            "PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD, or load .env first."
        )
    conn = psycopg.connect(**params, row_factory=dict_row)
    apply_search_path(conn)
    return conn


def export_case_trace_csv(root: Path, outputs_csv: Path, out_path: Path, limit: int = 500) -> dict[str, Any]:
    from psycopg import sql

    from postgres_schema_config import rpt_relation

    run_label = outputs_csv.relative_to(root).with_suffix("").as_posix() if outputs_csv.is_absolute() else outputs_csv.with_suffix("").as_posix()
    views = ["case_run_trace_reporting", "case_run_trace"]

    with connect_db() as db, db.cursor() as cur:
        for view in views:
            query = sql.SQL(
                "SELECT * FROM {} WHERE run_label = %s ORDER BY response_id DESC NULLS LAST LIMIT %s"
            ).format(rpt_relation(view))
            cur.execute(query, (run_label, limit))
            rows = cur.fetchall()
            if rows:
                fieldnames = list(rows[0].keys())
                out_path.parent.mkdir(parents=True, exist_ok=True)
                with out_path.open("w", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
                    writer.writeheader()
                    writer.writerows(rows)
                return {
                    "path": str(out_path),
                    "row_count": len(rows),
                    "view": f"rpt.{view}",
                    "run_label": run_label,
                }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("", encoding="utf-8")
    return {"path": str(out_path), "row_count": 0, "view": None, "run_label": run_label}


def read_csv_header(path: Path) -> list[str]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or [])


def assess_v5_trace(config: dict[str, Any], outputs_csv: Path, case_trace_csv: Path | None) -> dict[str, Any] | None:
    dataset_version = str(config.get("dataset_version", ""))
    if not dataset_version.startswith("v5_"):
        return None

    outputs_header = set(read_csv_header(outputs_csv))
    case_trace_header = set(read_csv_header(case_trace_csv)) if case_trace_csv else set()
    outputs_has_turn_fields = {"pressure_turns", "pressure_turn_count"}.issubset(outputs_header)
    case_trace_has_turn_fields = {"pressure_turns", "pressure_turn_count"}.issubset(case_trace_header)

    if case_trace_has_turn_fields:
        assessment = (
            "Reporting preserves the pressure-turn list and count, plus the final model output. "
            "It still does not expose each intermediate assistant completion as separate reporting rows; "
            "the Inspect .eval log remains the source of truth for the full multi-call transcript."
        )
    elif outputs_has_turn_fields:
        assessment = (
            "The exported Inspect CSV preserves pressure-turn list/count, but current reporting views do not. "
            "Ingestion is usable for final-output analysis only unless reporting is rebuilt with those fields."
        )
    else:
        assessment = (
            "Current export/reporting collapses v5 to final output and basic metadata. "
            "Use the .eval log for multi-turn inspection."
        )

    return {
        "outputs_csv_has_pressure_turn_fields": outputs_has_turn_fields,
        "case_trace_csv_has_pressure_turn_fields": case_trace_has_turn_fields,
        "assessment": assessment,
    }


def run_optional_audit(config: dict[str, Any], root: Path, output_dir: Path, summary: dict[str, Any]) -> None:
    audit = config.get("audit", False)
    if not audit:
        summary["audit"] = {"enabled": False}
        return

    audit_command = config.get("audit_command")
    if not audit_command:
        raise RuntimeError("audit=true requires audit_command. No separate schema-v2.1 audit script was inferred.")
    if isinstance(audit_command, str):
        cmd = shlex.split(audit_command)
    elif isinstance(audit_command, list):
        cmd = [str(part) for part in audit_command]
    else:
        raise ValueError("audit_command must be a string or list")

    placeholders = {
        "output_dir": str(output_dir),
        "outputs_csv": str(summary.get("outputs_csv", "")),
        "eval_log": str(summary.get("eval_log", "")),
    }
    cmd = [part.format(**placeholders) for part in cmd]
    summary.setdefault("steps", []).append(run_step(cmd, root, output_dir, "audit"))
    summary["audit"] = {"enabled": True, "command": shlex.join(cmd)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one small local experiment pipeline.")
    parser.add_argument("experiment", type=Path, help="Path to one experiments/*.yaml file.")
    args = parser.parse_args()

    root = ROOT
    load_env_file(root / ".env")
    config = load_experiment(args.experiment)
    name = require(config, "name")
    output_dir = root / Path(require(config, "output_dir"))
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "name": name,
        "experiment_file": str(args.experiment),
        "dataset_version": config.get("dataset_version"),
        "model": config.get("model"),
        "output_dir": str(output_dir),
        "steps": [],
    }

    summary_path = output_dir / "run_summary.json"

    try:
        inspect_cmd = build_inspect_command(config)
        started_at = time.time()
        summary["steps"].append(run_step(inspect_cmd, root, output_dir, "inspect_eval"))

        log_dir = Path(str(config.get("log_dir", DEFAULT_LOG_DIR)))
        log_path = locate_eval_log(root, log_dir, started_at)
        summary["eval_log"] = str(log_path)

        outputs_csv = root / Path(str(config.get("outputs_csv", default_outputs_csv(name))))
        if bool(config.get("export_outputs", config.get("ingest", True))):
            export_cmd = export_outputs(log_path, outputs_csv)
            summary["steps"].append(run_step(export_cmd, root, output_dir, "export_outputs"))
        summary["outputs_csv"] = str(outputs_csv)

        ingest_cmd = ingest_command(config)
        if ingest_cmd:
            summary["steps"].append(run_step(ingest_cmd, root, output_dir, "postgres_ingest_rebuild"))

        case_trace_csv: Path | None = None
        if bool(config.get("export_case_trace", False)):
            case_trace_csv = output_dir / "case_trace.csv"
            summary["case_trace_csv"] = export_case_trace_csv(
                root=root,
                outputs_csv=outputs_csv,
                out_path=case_trace_csv,
                limit=int(config.get("case_trace_limit", 500)),
            )

        run_optional_audit(config, root, output_dir, summary)
        v5_trace = assess_v5_trace(config, outputs_csv, case_trace_csv)
        if v5_trace:
            summary["v5_trace_preservation"] = v5_trace

        summary["status"] = "ok"
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(summary_path)
        return 0
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        print(summary_path, file=sys.stderr)
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
