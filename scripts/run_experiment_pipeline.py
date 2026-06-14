#!/usr/bin/env python
"""Thin local experiment pipeline around Inspect, Postgres, and derived exports."""

from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import os
import shutil
import shlex
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover - local environment guard
    raise SystemExit("Install dependency first: python -m pip install pyyaml") from exc


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = Path("logs")
MANIFEST_SQL = ROOT / "sql" / "019_create_experiment_manifest_tables.sql"
PIPELINE_LINK_SQL = ROOT / "sql" / "020_link_pipeline_runs_to_operational_tables.sql"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def experiment_slug(config: dict[str, Any]) -> str:
    return str(config.get("experiment_slug") or config.get("name") or "").strip()


def answer_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    configured = config.get("answer_models")
    if isinstance(configured, list) and configured:
        return [item for item in configured if isinstance(item, dict)]
    return [
        {
            "model": require(config, "model"),
            "model_args": config.get("model_args") or {},
        }
    ]


def primary_answer_model(config: dict[str, Any]) -> dict[str, Any]:
    models = answer_models(config)
    if not models:
        raise ValueError("At least one answer model is required.")
    return models[0]


def grader_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    configured = config.get("grader_models")
    return configured if isinstance(configured, list) else []


def scoring_config(config: dict[str, Any]) -> dict[str, Any]:
    scoring = config.get("scoring")
    if isinstance(scoring, dict):
        return scoring
    return {
        "deterministic_tuple_audit": bool(config.get("audit", False)),
        "ai_grading": False,
    }


def exports_config(config: dict[str, Any]) -> dict[str, Any]:
    exports = config.get("exports")
    if isinstance(exports, dict):
        return exports
    return {
        "outputs_csv": bool(config.get("export_outputs", config.get("ingest", True))),
        "csv_case_trace": bool(config.get("export_case_trace", False)),
        "markdown_summary": False,
        "case_cards": False,
    }


def provenance_config(config: dict[str, Any]) -> dict[str, Any]:
    provenance = config.get("provenance")
    if isinstance(provenance, dict):
        return provenance
    return {"hash_dataset": True, "hash_eval_log": True, "immutable_eval_log": True}


def cli_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def build_inspect_command(config: dict[str, Any]) -> list[str]:
    model_spec = primary_answer_model(config)
    cmd = [
        "inspect",
        "eval",
        require(config, "task"),
        "--model",
        str(model_spec.get("model") or require(config, "model")),
        "-T",
        f"dataset_version={require(config, 'dataset_version')}",
    ]

    for key, value in (model_spec.get("model_args") or {}).items():
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


def immutable_eval_log_copy(log_path: Path, output_dir: Path) -> Path:
    target_dir = output_dir / "inspect_logs"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / log_path.name
    if log_path.resolve() != target.resolve():
        shutil.copy2(log_path, target)
    return target


def default_outputs_csv(slug: str) -> Path:
    # Keep this one level under tmp/ so the existing ingest allowlist picks it up.
    return Path("tmp") / f"{slug}_export.csv"


def export_outputs(log_path: Path, outputs_csv: Path) -> list[str]:
    return [
        sys.executable,
        "src/moral_sycophancy_eval/export_behaviour_outputs.py",
        str(log_path),
        "--csv",
        str(outputs_csv),
    ]


def export_outputs_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("export_outputs", exports_config(config).get("outputs_csv", config.get("ingest", True))))


def export_case_trace_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("export_case_trace", exports_config(config).get("csv_case_trace", False)))


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


def resolve_repo_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value)
    return path if path.is_absolute() else ROOT / path


def sha256_file(path: Path | None) -> str | None:
    if not path or not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


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


def ensure_manifest_tables() -> None:
    with connect_db() as db, db.cursor() as cur:
        cur.execute(MANIFEST_SQL.read_text(encoding="utf-8"))
        db.commit()


def link_pipeline_operational_tables() -> None:
    with connect_db() as db, db.cursor() as cur:
        cur.execute(PIPELINE_LINK_SQL.read_text(encoding="utf-8"))
        db.commit()


def upsert_manifest(config: dict[str, Any], manifest_path: Path, manifest_sha: str | None, dataset_sha: str | None) -> int:
    slug = experiment_slug(config)
    dataset_file = config.get("dataset_file")
    with connect_db() as db, db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.experiment_manifest AS em (
                experiment_slug, name, dataset_version, dataset_alias, dataset_file_path,
                dataset_file_sha256, prompt_style, task, answer_models, grader_models,
                scoring, exports, provenance, limit_count, output_dir, log_dir,
                ingest, rebuild_derived, manifest_file_path, manifest_file_sha256, raw_manifest
            ) VALUES (
                %s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,
                %s,%s,%s,%s,%s,%s,%s,%s::jsonb
            )
            ON CONFLICT (experiment_slug) DO UPDATE SET
                name = excluded.name,
                dataset_version = excluded.dataset_version,
                dataset_alias = excluded.dataset_alias,
                dataset_file_path = excluded.dataset_file_path,
                dataset_file_sha256 = excluded.dataset_file_sha256,
                prompt_style = excluded.prompt_style,
                task = excluded.task,
                answer_models = excluded.answer_models,
                grader_models = excluded.grader_models,
                scoring = excluded.scoring,
                exports = excluded.exports,
                provenance = excluded.provenance,
                limit_count = excluded.limit_count,
                output_dir = excluded.output_dir,
                log_dir = excluded.log_dir,
                ingest = excluded.ingest,
                rebuild_derived = excluded.rebuild_derived,
                manifest_file_path = excluded.manifest_file_path,
                manifest_file_sha256 = excluded.manifest_file_sha256,
                raw_manifest = excluded.raw_manifest,
                updated_at = now()
            RETURNING experiment_manifest_id
            """,
            (
                slug,
                config.get("name") or slug,
                require(config, "dataset_version"),
                config.get("dataset_alias"),
                dataset_file,
                dataset_sha,
                config.get("prompt_style"),
                require(config, "task"),
                json_text(answer_models(config)),
                json_text(grader_models(config)),
                json_text(scoring_config(config)),
                json_text(exports_config(config)),
                json_text(provenance_config(config)),
                config.get("limit"),
                config.get("output_dir"),
                config.get("log_dir"),
                bool(config.get("ingest", True)),
                bool(config.get("rebuild_derived", True)),
                manifest_path.as_posix(),
                manifest_sha,
                json_text(config),
            ),
        )
        manifest_id = int(cur.fetchone()["experiment_manifest_id"])
        db.commit()
        return manifest_id


def create_pipeline_run(config: dict[str, Any], manifest_id: int, pipeline_run_key: str, inspect_cmd: list[str]) -> int:
    model_spec = primary_answer_model(config)
    with connect_db() as db, db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO public.experiment_pipeline_run (
                experiment_manifest_id, pipeline_run_key, experiment_slug, status,
                task, dataset_version, dataset_alias, prompt_style, answer_model,
                answer_models, grader_models, scoring, exports, provenance,
                limit_count, output_dir, inspect_command, raw_run_metadata
            ) VALUES (
                %s,%s,%s,'running',%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,
                %s,%s,%s,%s::jsonb
            )
            RETURNING experiment_pipeline_run_id
            """,
            (
                manifest_id,
                pipeline_run_key,
                experiment_slug(config),
                require(config, "task"),
                require(config, "dataset_version"),
                config.get("dataset_alias"),
                config.get("prompt_style"),
                json_text(model_spec),
                json_text(answer_models(config)),
                json_text(grader_models(config)),
                json_text(scoring_config(config)),
                json_text(exports_config(config)),
                json_text(provenance_config(config)),
                config.get("limit"),
                config.get("output_dir"),
                shlex.join(inspect_cmd),
                json_text({"started_at": utc_now()}),
            ),
        )
        run_id = int(cur.fetchone()["experiment_pipeline_run_id"])
        db.commit()
        return run_id


def update_pipeline_run(run_id: int | None, **fields: Any) -> None:
    if not run_id or not fields:
        return

    allowed = {
        "status",
        "eval_log_path",
        "eval_log_sha256",
        "original_eval_log_path",
        "outputs_csv_path",
        "outputs_csv_sha256",
        "case_trace_csv_path",
        "case_trace_csv_row_count",
        "run_summary_path",
        "run_summary",
        "error",
        "raw_run_metadata",
        "completed_at",
    }
    assignments = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            raise ValueError(f"Unsupported experiment_pipeline_run field: {key}")
        if key in {"run_summary", "raw_run_metadata"}:
            assignments.append(f"{key} = %s::jsonb")
            values.append(json_text(value))
        else:
            assignments.append(f"{key} = %s")
            values.append(value)
    assignments.append("updated_at = now()")
    values.append(run_id)

    with connect_db() as db, db.cursor() as cur:
        cur.execute(
            f"UPDATE public.experiment_pipeline_run SET {', '.join(assignments)} WHERE experiment_pipeline_run_id = %s",
            values,
        )
        db.commit()


def get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_jsonable(v, depth + 1) for v in value]
    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(value), depth + 1)
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump(), depth + 1)
    if hasattr(value, "dict"):
        try:
            return to_jsonable(value.dict(), depth + 1)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return to_jsonable(vars(value), depth + 1)
    return str(value)


def extract_completion(sample: Any) -> str:
    output = get_attr_or_key(sample, "output", None)
    completion = get_attr_or_key(output, "completion", "")
    if completion:
        return str(completion)

    messages = get_attr_or_key(sample, "messages", []) or []
    for message in reversed(messages):
        role = str(get_attr_or_key(message, "role", "")).lower()
        content = get_attr_or_key(message, "content", "")
        if role == "assistant" and content:
            return str(content)
    return ""


def ingest_inspect_log_samples(run_id: int, log_path: Path, log_sha: str | None, dataset_version: str) -> int:
    from inspect_ai.log import read_eval_log

    log = read_eval_log(log_path)
    samples = get_attr_or_key(log, "samples", []) or []
    count = 0

    with connect_db() as db, db.cursor() as cur:
        for sample in samples:
            sample_id = str(get_attr_or_key(sample, "id", "")) or f"sample-{count + 1}"
            metadata = get_attr_or_key(sample, "metadata", {}) or {}
            output = get_attr_or_key(sample, "output", None)
            messages = get_attr_or_key(sample, "messages", []) or []
            scores = get_attr_or_key(sample, "scores", {}) or {}
            usage = get_attr_or_key(output, "usage", {}) or get_attr_or_key(sample, "usage", {}) or {}
            input_text = get_attr_or_key(sample, "input", "")
            target_text = get_attr_or_key(sample, "target", "")
            final_response = extract_completion(sample)

            cur.execute(
                """
                INSERT INTO public.inspect_log_sample AS ils (
                    experiment_pipeline_run_id, sample_id, dataset_version, input_text,
                    target_text, final_response, metadata, messages, usage, scores,
                    raw_sample, source_log_path, source_log_sha256
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s
                )
                ON CONFLICT (experiment_pipeline_run_id, sample_id) DO UPDATE SET
                    dataset_version = excluded.dataset_version,
                    input_text = excluded.input_text,
                    target_text = excluded.target_text,
                    final_response = excluded.final_response,
                    metadata = excluded.metadata,
                    messages = excluded.messages,
                    usage = excluded.usage,
                    scores = excluded.scores,
                    raw_sample = excluded.raw_sample,
                    source_log_path = excluded.source_log_path,
                    source_log_sha256 = excluded.source_log_sha256,
                    updated_at = now()
                """,
                (
                    run_id,
                    sample_id,
                    str(metadata.get("dataset_version") or dataset_version),
                    str(input_text) if input_text is not None else None,
                    str(target_text) if target_text is not None else None,
                    final_response,
                    json_text(to_jsonable(metadata)),
                    json_text(to_jsonable(messages)),
                    json_text(to_jsonable(usage)),
                    json_text(to_jsonable(scores)),
                    json_text(to_jsonable(sample)),
                    log_path.as_posix(),
                    log_sha,
                ),
            )
            count += 1
        db.commit()
    return count


def export_case_trace_csv(root: Path, outputs_csv: Path, out_path: Path, limit: int = 500) -> dict[str, Any]:
    from psycopg import sql

    from postgres_schema_config import rpt_relation

    if outputs_csv.is_absolute():
        run_label = outputs_csv.relative_to(root).with_suffix("").as_posix()
    else:
        run_label = outputs_csv.with_suffix("").as_posix()
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


def read_csv_header(path: Path | None) -> list[str]:
    if not path or not path.exists() or path.stat().st_size == 0:
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
            "Intermediate assistant completions are stored in public.inspect_log_sample.raw_sample/messages; "
            "the immutable copied .eval log remains the strongest raw evidence artefact."
        )
    elif outputs_has_turn_fields:
        assessment = (
            "The exported Inspect CSV preserves pressure-turn list/count, but current reporting views do not. "
            "Use public.inspect_log_sample and the copied .eval log for transcript inspection."
        )
    else:
        assessment = (
            "CSV/reporting collapses v5 to final output and basic metadata. "
            "Use public.inspect_log_sample and the copied .eval log for transcript inspection."
        )

    return {
        "outputs_csv_has_pressure_turn_fields": outputs_has_turn_fields,
        "case_trace_csv_has_pressure_turn_fields": case_trace_has_turn_fields,
        "assessment": assessment,
    }


def run_optional_audit(config: dict[str, Any], root: Path, output_dir: Path, summary: dict[str, Any]) -> None:
    audit = bool(config.get("audit", False))
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
    slug = experiment_slug(config)
    if not slug:
        raise SystemExit("Experiment config must define experiment_slug or name.")

    output_dir = root / Path(require(config, "output_dir"))
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "run_summary.json"
    manifest_path = args.experiment if args.experiment.is_absolute() else root / args.experiment
    dataset_path = resolve_repo_path(config.get("dataset_file"))
    manifest_sha = sha256_file(manifest_path)
    dataset_sha = sha256_file(dataset_path)
    pipeline_run_key = f"{slug}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

    inspect_cmd = build_inspect_command(config)
    run_id: int | None = None
    case_trace_csv: Path | None = None

    summary: dict[str, Any] = {
        "experiment_slug": slug,
        "name": config.get("name") or slug,
        "pipeline_run_key": pipeline_run_key,
        "experiment_file": str(args.experiment),
        "experiment_manifest_sha256": manifest_sha,
        "dataset_version": config.get("dataset_version"),
        "dataset_alias": config.get("dataset_alias"),
        "dataset_file": config.get("dataset_file"),
        "dataset_sha256": dataset_sha,
        "prompt_style": config.get("prompt_style"),
        "task": config.get("task"),
        "answer_models": answer_models(config),
        "grader_models": grader_models(config),
        "scoring": scoring_config(config),
        "exports": exports_config(config),
        "provenance": provenance_config(config),
        "limit": config.get("limit"),
        "output_dir": str(output_dir),
        "steps": [],
    }

    try:
        ensure_manifest_tables()
        manifest_id = upsert_manifest(config, manifest_path, manifest_sha, dataset_sha)
        run_id = create_pipeline_run(config, manifest_id, pipeline_run_key, inspect_cmd)
        summary["experiment_manifest_id"] = manifest_id
        summary["experiment_pipeline_run_id"] = run_id

        started_at = time.time()
        summary["steps"].append(run_step(inspect_cmd, root, output_dir, "inspect_eval"))

        log_dir = Path(str(config.get("log_dir", DEFAULT_LOG_DIR)))
        original_log_path = locate_eval_log(root, log_dir, started_at)
        immutable_log_path = immutable_eval_log_copy(original_log_path, output_dir)
        eval_log_sha = sha256_file(immutable_log_path)
        summary["original_eval_log"] = str(original_log_path)
        summary["eval_log"] = str(immutable_log_path)
        summary["eval_log_sha256"] = eval_log_sha

        inspect_sample_count = ingest_inspect_log_samples(
            run_id=run_id,
            log_path=immutable_log_path,
            log_sha=eval_log_sha,
            dataset_version=require(config, "dataset_version"),
        )
        summary["inspect_log_samples_ingested"] = inspect_sample_count

        outputs_csv = root / Path(str(config.get("outputs_csv", default_outputs_csv(slug))))
        if export_outputs_enabled(config):
            export_cmd = export_outputs(immutable_log_path, outputs_csv)
            summary["steps"].append(run_step(export_cmd, root, output_dir, "export_outputs"))
        outputs_csv_sha = sha256_file(outputs_csv)
        summary["outputs_csv"] = str(outputs_csv)
        summary["outputs_csv_sha256"] = outputs_csv_sha

        ingest_cmd = ingest_command(config)
        if ingest_cmd:
            summary["steps"].append(run_step(ingest_cmd, root, output_dir, "postgres_ingest_rebuild"))
            link_pipeline_operational_tables()

        if export_case_trace_enabled(config):
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
        update_pipeline_run(
            run_id,
            status="ok",
            eval_log_path=str(immutable_log_path),
            eval_log_sha256=eval_log_sha,
            original_eval_log_path=str(original_log_path),
            outputs_csv_path=str(outputs_csv),
            outputs_csv_sha256=outputs_csv_sha,
            case_trace_csv_path=str(case_trace_csv) if case_trace_csv else None,
            case_trace_csv_row_count=(summary.get("case_trace_csv") or {}).get("row_count"),
            run_summary_path=str(summary_path),
            run_summary=summary,
            raw_run_metadata={"completed_at": utc_now()},
            completed_at=utc_now(),
        )
        print(summary_path)
        return 0
    except Exception as exc:
        summary["status"] = "failed"
        summary["error"] = str(exc)
        summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        update_pipeline_run(
            run_id,
            status="failed",
            error=str(exc),
            run_summary_path=str(summary_path),
            run_summary=summary,
            raw_run_metadata={"failed_at": utc_now()},
            completed_at=utc_now(),
        )
        print(summary_path, file=sys.stderr)
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
