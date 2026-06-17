#!/usr/bin/env python
"""Backfill public Inspect-log provenance tables from existing .eval files.

This repairs historical runs where the durable Inspect logs exist on disk but
public.experiment_pipeline_run and public.inspect_log_sample were only populated
for newer pipeline-managed runs.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from moral_sycophancy_eval.diagnostics import normalise_diagnostics_config  # noqa: E402
from run_experiment_pipeline import (  # noqa: E402
    upsert_model_call_diagnostics,
    upsert_response_diagnostics,
)

MANIFEST_SQL = ROOT / "sql" / "019_create_experiment_manifest_tables.sql"
PIPELINE_LINK_SQL = ROOT / "sql" / "020_link_pipeline_runs_to_operational_tables.sql"
RESPONSE_DIAGNOSTICS_SQL = ROOT / "sql" / "021_create_response_diagnostics.sql"
MODEL_CALL_DIAGNOSTICS_SQL = ROOT / "sql" / "022_create_model_call_diagnostics.sql"
DERIVED_REBUILD_SQL = (
    ROOT / "sql" / "promote_public_dimensions_from_raw.sql",
    ROOT / "sql" / "backfill_public_operational_from_raw.sql",
    ROOT / "sql" / "promote_public_expectations_and_decisions_from_raw.sql",
    ROOT / "sql" / "create_reporting_views.sql",
)
DATABASE_URL_ENV = "MORAL_EVALS_DATABASE_URL"


def load_env_file(path: Path) -> None:
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
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def connect_db():
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - local environment guard
        raise SystemExit('Install dependency first: python -m pip install "psycopg[binary]"') from exc

    from postgres_schema_config import apply_search_path

    load_env_file(ROOT / ".env")
    dsn = os.getenv(DATABASE_URL_ENV)
    if not dsn:
        raise SystemExit(f"{DATABASE_URL_ENV} must be set or present in .env.")
    conn = psycopg.connect(dsn, row_factory=dict_row)
    apply_search_path(conn)
    return conn


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


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def jsonish(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return default
    try:
        return json.loads(text) if text[0] in "[{" else ([text] if isinstance(default, list) else default)
    except json.JSONDecodeError:
        return default


def sha256_file(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "unknown"


def repo_display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def repo_stem_label(path: Path) -> str:
    try:
        relative = path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        relative = path.resolve()
    return relative.with_suffix("").as_posix()


def dataset_family(dataset_version: str) -> str:
    if "release_governance" in dataset_version:
        return "release_governance"
    if "scope_control" in dataset_version:
        return "scope_control"
    if "trap_expansion" in dataset_version:
        return "evidence_strength_trap_expansion"
    if "evidence_strength" in dataset_version:
        return "evidence_strength"
    if "integrity" in dataset_version:
        return "integrity"
    return "behavioural"


def provider_from_model(model_name: str | None) -> str | None:
    if not model_name:
        return None
    if "/" in model_name:
        provider = model_name.split("/", 1)[0].strip()
        return provider or None
    lowered = model_name.lower()
    if lowered.startswith("gpt-") or lowered.startswith("o"):
        return "openai"
    if lowered.startswith("claude"):
        return "anthropic"
    if lowered.startswith("gemini"):
        return "google"
    return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def discover_eval_logs(paths: list[Path]) -> list[Path]:
    found: list[Path] = []
    for raw_path in paths:
        path = raw_path if raw_path.is_absolute() else ROOT / raw_path
        if path.is_file() and path.suffix == ".eval":
            found.append(path)
        elif path.is_dir():
            found.extend(sorted(path.rglob("*.eval")))
    return sorted(set(found), key=lambda item: item.stat().st_mtime)


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


def sample_identifier(sample: Any, ordinal: int = 0) -> str | None:
    direct = clean(get_attr_or_key(sample, "id", None))
    if direct:
        return direct
    metadata = get_attr_or_key(sample, "metadata", {}) or {}
    for key in ("sample_id", "id", "case_id", "source_item_id"):
        value = clean(get_attr_or_key(metadata, key, None))
        if value:
            return value
    if ordinal:
        return f"sample-{ordinal}"
    return None


def stringify_input(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return clean(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            role = get_attr_or_key(item, "role", None)
            content = get_attr_or_key(item, "content", None)
            if content:
                prefix = f"{role}: " if role else ""
                parts.append(f"{prefix}{content}")
        if parts:
            return clean("\n\n".join(parts))
    return clean(str(value))


def sample_metadata(sample: Any) -> dict[str, Any]:
    metadata = get_attr_or_key(sample, "metadata", {}) or {}
    jsonable = to_jsonable(metadata)
    return jsonable if isinstance(jsonable, dict) else {}


def sample_dataset_version(sample: Any, summary: dict[str, Any]) -> str | None:
    metadata = sample_metadata(sample)
    return clean(metadata.get("dataset_version") or summary.get("dataset_version"))


def is_promotable_sample(sample: Any, summary: dict[str, Any], ordinal: int = 0) -> tuple[bool, str | None]:
    if not clean(summary.get("dataset_version")):
        return False, "missing_dataset_version"
    if not sample_identifier(sample, ordinal):
        return False, "missing_sample_id"
    if not clean(extract_completion(sample)):
        return False, "missing_final_response"
    return True, None


def most_common(values: list[str | None], default: str | None = None) -> str | None:
    cleaned = [value for value in values if value]
    if not cleaned:
        return default
    return Counter(cleaned).most_common(1)[0][0]


def log_model_name(log: Any) -> str | None:
    for path in (
        ("eval", "model"),
        ("eval", "model_args", "model"),
        ("plan", "model"),
        ("stats", "model"),
    ):
        value = log
        for part in path:
            value = get_attr_or_key(value, part, None)
            if value is None:
                break
        if value:
            return str(value)
    return None


def log_task_name(log: Any) -> str | None:
    for path in (("eval", "task"), ("eval", "task_name"), ("plan", "task")):
        value = log
        for part in path:
            value = get_attr_or_key(value, part, None)
            if value is None:
                break
        if value:
            return str(value)
    return None


def log_task_arg(log: Any, name: str) -> str | None:
    eval_info = get_attr_or_key(log, "eval", None)
    for field in ("task_args", "task_args_passed"):
        values = get_attr_or_key(eval_info, field, None)
        if isinstance(values, dict) and values.get(name):
            return str(values[name])
    return None


def log_timestamp(log: Any) -> datetime | None:
    for path in (
        ("eval", "created"),
        ("eval", "started_at"),
        ("eval", "completed_at"),
        ("stats", "started_at"),
        ("stats", "completed_at"),
    ):
        value = log
        for part in path:
            value = get_attr_or_key(value, part, None)
            if value is None:
                break
        parsed = parse_datetime(value)
        if parsed:
            return parsed
    return None


def summarise_log(path: Path) -> dict[str, Any]:
    from inspect_ai.log import read_eval_log

    log = read_eval_log(path)
    samples = list(get_attr_or_key(log, "samples", []) or [])
    sample_metadata = [get_attr_or_key(sample, "metadata", {}) or {} for sample in samples]
    dataset_version = most_common(
        [str(metadata.get("dataset_version")) for metadata in sample_metadata if metadata.get("dataset_version")]
    ) or log_task_arg(log, "dataset_version")
    prompt_style = most_common(
        [str(metadata.get("prompt_style")) for metadata in sample_metadata if metadata.get("prompt_style")]
    ) or log_task_arg(log, "prompt_style")
    model_name = log_model_name(log)
    task_name = log_task_name(log)
    status = str(get_attr_or_key(log, "status", "ok") or "ok").lower()
    if status in {"success", "completed", "complete"}:
        status = "ok"

    return {
        "path": path,
        "sha256": sha256_file(path),
        "log": log,
        "samples": samples,
        "dataset_version": dataset_version,
        "prompt_style": prompt_style,
        "model_name": model_name,
        "task": task_name,
        "status": status or "ok",
        "sample_count": len(samples),
        "run_timestamp": log_timestamp(log),
    }


def ensure_tables_and_linker(cur) -> None:
    cur.execute(MANIFEST_SQL.read_text(encoding="utf-8"))
    cur.execute(PIPELINE_LINK_SQL.read_text(encoding="utf-8"))


def ensure_diagnostics_tables(cur) -> None:
    cur.execute(RESPONSE_DIAGNOSTICS_SQL.read_text(encoding="utf-8"))
    cur.execute(MODEL_CALL_DIAGNOSTICS_SQL.read_text(encoding="utf-8"))


def execute_derived_rebuild(cur) -> None:
    for path in DERIVED_REBUILD_SQL:
        cur.execute(path.read_text(encoding="utf-8"))
    cur.execute(PIPELINE_LINK_SQL.read_text(encoding="utf-8"))


def upsert_backfill_manifest(cur, summary: dict[str, Any]) -> int:
    path = summary["path"]
    sha = summary["sha256"]
    dataset_version = summary["dataset_version"] or "unknown"
    model_slug = slugify(summary["model_name"] or path.stem)
    slug = f"inspect-log-backfill-{slugify(dataset_version)}-{model_slug}-{sha[:12]}"
    pipeline_run_key = f"inspect-log-backfill-{sha[:20]}"
    raw_manifest = {
        "backfill_source": "inspect_eval_log",
        "source_log_path": repo_display_path(path),
        "source_log_sha256": sha,
        "dataset_version": summary["dataset_version"],
        "prompt_style": summary["prompt_style"],
        "model_name": summary["model_name"],
        "task": summary["task"],
        "sample_count": summary["sample_count"],
    }

    cur.execute(
        """
        SELECT experiment_manifest_id
        FROM public.experiment_pipeline_run
        WHERE pipeline_run_key = %s
          AND experiment_manifest_id IS NOT NULL
        LIMIT 1
        """,
        (pipeline_run_key,),
    )
    existing = cur.fetchone()
    if existing:
        manifest_id = int(existing["experiment_manifest_id"])
        cur.execute(
            """
            UPDATE public.experiment_manifest
            SET
                name = %s,
                dataset_version = %s,
                prompt_style = %s,
                task = %s,
                answer_models = %s::jsonb,
                raw_manifest = %s::jsonb,
                updated_at = now()
            WHERE experiment_manifest_id = %s
            RETURNING experiment_manifest_id
            """,
            (
                f"Inspect log backfill: {path.name}",
                dataset_version,
                summary["prompt_style"],
                summary["task"] or "unknown_inspect_task",
                json_text([{"model": summary["model_name"]}] if summary["model_name"] else []),
                json_text(raw_manifest),
                manifest_id,
            ),
        )
        return int(cur.fetchone()["experiment_manifest_id"])

    cur.execute(
        """
        INSERT INTO public.experiment_manifest AS em (
            experiment_slug,
            name,
            dataset_version,
            prompt_style,
            task,
            answer_models,
            raw_manifest
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
        ON CONFLICT (experiment_slug) DO UPDATE SET
            name = excluded.name,
            dataset_version = excluded.dataset_version,
            prompt_style = excluded.prompt_style,
            task = excluded.task,
            answer_models = excluded.answer_models,
            raw_manifest = excluded.raw_manifest,
            updated_at = now()
        RETURNING experiment_manifest_id
        """,
        (
            slug,
            f"Inspect log backfill: {path.name}",
            dataset_version,
            summary["prompt_style"],
            summary["task"] or "unknown_inspect_task",
            json_text([{"model": summary["model_name"]}] if summary["model_name"] else []),
            json_text(raw_manifest),
        ),
    )
    return int(cur.fetchone()["experiment_manifest_id"])


def upsert_pipeline_run(
    cur,
    summary: dict[str, Any],
    manifest_id: int,
    diagnostics_config: dict[str, Any] | None = None,
) -> int:
    path = summary["path"]
    sha = summary["sha256"]
    dataset_version = summary["dataset_version"]
    pipeline_run_key = f"inspect-log-backfill-{sha[:20]}"
    raw_run_metadata = backfill_run_metadata(summary, diagnostics_config)
    answer_model = {"model": summary["model_name"]} if summary["model_name"] else {}

    cur.execute(
        """
        SELECT experiment_pipeline_run_id, pipeline_run_key
        FROM public.experiment_pipeline_run
        WHERE eval_log_sha256 = %s
        ORDER BY
            CASE WHEN pipeline_run_key LIKE 'inspect-log-backfill-%%' THEN 1 ELSE 0 END,
            CASE WHEN experiment_manifest_id IS NULL THEN 1 ELSE 0 END,
            experiment_pipeline_run_id
        LIMIT 1
        """,
        (sha,),
    )
    canonical = cur.fetchone()
    if canonical and canonical["pipeline_run_key"] != pipeline_run_key:
        cur.execute(
            """
            UPDATE public.experiment_pipeline_run
            SET
                status = COALESCE(status, %s),
                task = COALESCE(task, %s),
                dataset_version = COALESCE(dataset_version, %s),
                prompt_style = COALESCE(prompt_style, %s),
                answer_model = CASE WHEN answer_model = '{}'::jsonb THEN %s::jsonb ELSE answer_model END,
                answer_models = CASE WHEN answer_models = '[]'::jsonb THEN %s::jsonb ELSE answer_models END,
                eval_log_sha256 = %s,
                original_eval_log_path = COALESCE(original_eval_log_path, %s),
                raw_run_metadata = COALESCE(raw_run_metadata, '{}'::jsonb) || %s::jsonb,
                completed_at = COALESCE(completed_at, now()),
                updated_at = now()
            WHERE experiment_pipeline_run_id = %s
            RETURNING experiment_pipeline_run_id
            """,
            (
                summary["status"],
                summary["task"] or "unknown_inspect_task",
                dataset_version,
                summary["prompt_style"],
                json_text(answer_model),
                json_text([answer_model] if answer_model else []),
                sha,
                repo_display_path(path),
                json_text(raw_run_metadata),
                canonical["experiment_pipeline_run_id"],
            ),
        )
        return int(cur.fetchone()["experiment_pipeline_run_id"])

    cur.execute(
        """
        INSERT INTO public.experiment_pipeline_run AS epr (
            experiment_manifest_id,
            pipeline_run_key,
            experiment_slug,
            status,
            task,
            dataset_version,
            prompt_style,
            answer_model,
            answer_models,
            eval_log_path,
            eval_log_sha256,
            original_eval_log_path,
            raw_run_metadata,
            completed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb, now())
        ON CONFLICT (pipeline_run_key) DO UPDATE SET
            experiment_manifest_id = excluded.experiment_manifest_id,
            status = excluded.status,
            task = excluded.task,
            dataset_version = excluded.dataset_version,
            prompt_style = excluded.prompt_style,
            answer_model = excluded.answer_model,
            answer_models = excluded.answer_models,
            eval_log_path = excluded.eval_log_path,
            eval_log_sha256 = excluded.eval_log_sha256,
            original_eval_log_path = excluded.original_eval_log_path,
            raw_run_metadata = excluded.raw_run_metadata,
            completed_at = excluded.completed_at,
            updated_at = now()
        RETURNING experiment_pipeline_run_id
        """,
        (
            manifest_id,
            pipeline_run_key,
            f"inspect-log-backfill-{slugify(dataset_version or 'unknown')}",
            summary["status"],
            summary["task"] or "unknown_inspect_task",
            dataset_version,
            summary["prompt_style"],
            json_text(answer_model),
            json_text([answer_model] if answer_model else []),
            repo_display_path(path),
            sha,
            repo_display_path(path),
            json_text(raw_run_metadata),
        ),
    )
    return int(cur.fetchone()["experiment_pipeline_run_id"])


def backfill_run_metadata(
    summary: dict[str, Any],
    diagnostics_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = summary["path"]
    sha = summary["sha256"]
    raw_run_metadata = {
        "backfill_source": "inspect_eval_log",
        "source_log_path": repo_display_path(path),
        "source_log_sha256": sha,
        "eval_log_path": repo_display_path(path),
        "model_name": summary["model_name"],
        "task": summary["task"],
        "sample_count": summary["sample_count"],
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
    }
    if diagnostics_config is not None:
        raw_run_metadata["diagnostics"] = diagnostics_config
        raw_run_metadata["diagnostics_backfill_source"] = "existing_eval_log"
    return raw_run_metadata


def insert_samples(cur, pipeline_run_id: int, summary: dict[str, Any]) -> int:
    count = 0
    path = summary["path"]
    sha = summary["sha256"]
    for sample in summary["samples"]:
        sample_id = sample_identifier(sample, count + 1) or f"sample-{count + 1}"
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
                experiment_pipeline_run_id,
                sample_id,
                dataset_version,
                input_text,
                target_text,
                final_response,
                metadata,
                messages,
                usage,
                scores,
                raw_sample,
                source_log_path,
                source_log_sha256
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
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
                pipeline_run_id,
                sample_id,
                str(metadata.get("dataset_version") or summary["dataset_version"] or ""),
                str(input_text) if input_text is not None else None,
                str(target_text) if target_text is not None else None,
                final_response,
                json_text(to_jsonable(metadata)),
                json_text(to_jsonable(messages)),
                json_text(to_jsonable(usage)),
                json_text(to_jsonable(scores)),
                json_text(to_jsonable(sample)),
                repo_display_path(path),
                sha,
            ),
        )
        count += 1
    return count


def diagnostics_write_enabled(args: argparse.Namespace) -> bool:
    return bool(args.diagnostics and (args.write or args.promote_operational))


def diagnostics_dry_run(args: argparse.Namespace) -> bool:
    return bool(args.diagnostics and not (args.write or args.promote_operational))


def empty_diagnostics_summary(requested: bool, dry_run: bool) -> dict[str, Any]:
    return {
        "diagnostics_requested": requested,
        "diagnostics_dry_run": dry_run,
        "inspect_samples_inserted_or_updated": 0,
        "response_diagnostics_upserted": 0,
        "model_call_diagnostics_upserted": 0,
        "model_call_diagnostics_exact_linked": 0,
        "model_call_diagnostics_unverified": 0,
    }


def diagnostics_linkage_counts(run_ids: list[int]) -> dict[str, int]:
    if not run_ids:
        return {
            "model_call_diagnostics_exact_linked": 0,
            "model_call_diagnostics_unverified": 0,
        }

    with connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*) FILTER (WHERE link_confidence = 'exact') AS exact_linked,
                count(*) FILTER (WHERE link_confidence = 'unverified') AS unverified
            FROM public.model_call_diagnostic
            WHERE experiment_pipeline_run_id = ANY(%s)
            """,
            (run_ids,),
        )
        row = cur.fetchone() or {}
    return {
        "model_call_diagnostics_exact_linked": int(row.get("exact_linked") or 0),
        "model_call_diagnostics_unverified": int(row.get("unverified") or 0),
    }


def run_diagnostics_backfill(run_ids: list[int], diagnostics_config: dict[str, Any]) -> dict[str, int]:
    response_count = 0
    model_call_count = 0
    for run_id in run_ids:
        response_count += upsert_response_diagnostics(run_id, diagnostics_config)
        model_call_count += upsert_model_call_diagnostics(run_id, diagnostics_config)
    counts = diagnostics_linkage_counts(run_ids)
    return {
        "response_diagnostics_upserted": response_count,
        "model_call_diagnostics_upserted": model_call_count,
        **counts,
    }


def source_file(cur, summary: dict[str, Any]) -> int:
    from psycopg import sql
    from postgres_schema_config import raw_relation

    path = summary["path"]
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {} AS source_file (
                file_path,
                file_kind,
                content_sha256,
                file_size_bytes,
                record_count
            )
            VALUES (%s, 'inspect_eval_artifact', %s, %s, %s)
            ON CONFLICT (file_path) DO UPDATE SET
                file_kind = EXCLUDED.file_kind,
                content_sha256 = EXCLUDED.content_sha256,
                file_size_bytes = EXCLUDED.file_size_bytes,
                record_count = EXCLUDED.record_count,
                ingested_at = now()
            RETURNING source_file_id
            """
        ).format(raw_relation("source_file")),
        (repo_display_path(path), summary["sha256"], path.stat().st_size, summary["sample_count"]),
    )
    return int(cur.fetchone()["source_file_id"])


def raw_dataset(cur, dataset_version: str, source_file_id: int) -> int:
    from psycopg import sql
    from postgres_schema_config import raw_relation

    metadata = {
        "backfill_source": "inspect_eval_log",
        "source_file_note": "dataset observed in Inspect eval log metadata",
    }
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {} AS dataset (
                dataset_version,
                dataset_family,
                source_file_id,
                raw_metadata
            )
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (dataset_version) DO UPDATE SET
                dataset_family = COALESCE(dataset.dataset_family, EXCLUDED.dataset_family),
                source_file_id = COALESCE(dataset.source_file_id, EXCLUDED.source_file_id),
                raw_metadata = COALESCE(dataset.raw_metadata, '{{}}'::jsonb) || EXCLUDED.raw_metadata,
                updated_at = now()
            RETURNING dataset_id
            """
        ).format(raw_relation("dataset")),
        (dataset_version, dataset_family(dataset_version), source_file_id, json_text(metadata)),
    )
    return int(cur.fetchone()["dataset_id"])


def case_record_from_sample(sample: Any, summary: dict[str, Any], ordinal: int) -> dict[str, Any]:
    metadata = sample_metadata(sample)
    input_text = stringify_input(get_attr_or_key(sample, "input", None))
    scenario = clean(metadata.get("scenario") or metadata.get("initial_scenario") or input_text)
    return {
        "sample_id": sample_identifier(sample, ordinal),
        "case_id": clean(metadata.get("case_id")),
        "source_item_id": clean(metadata.get("source_item_id")),
        "variant": clean(metadata.get("variant")),
        "prompt_style": clean(metadata.get("prompt_style") or summary.get("prompt_style")),
        "moral_domain": clean(metadata.get("moral_domain")),
        "risk_track": clean(metadata.get("risk_track")),
        "scenario": scenario,
        "initial_judgement": clean(metadata.get("initial_judgement")),
        "difficulty": clean(metadata.get("difficulty")),
        "difficulty_notes": clean(metadata.get("difficulty_notes")),
        "raw_record": {
            "backfill_source": "inspect_eval_log",
            "scenario_source": "metadata.scenario" if metadata.get("scenario") else "inspect_sample_input",
            "exact_source_text": bool(metadata.get("scenario") or input_text),
            "inspect_metadata": metadata,
            "inspect_input": to_jsonable(get_attr_or_key(sample, "input", None)),
            "inspect_target": to_jsonable(get_attr_or_key(sample, "target", None)),
            "source_log_path": repo_display_path(summary["path"]),
            "source_log_sha256": summary["sha256"],
        },
    }


def raw_dataset_case(cur, dataset_id: int, source_file_id: int, record: dict[str, Any]) -> int:
    from psycopg import sql
    from postgres_schema_config import raw_relation

    cur.execute(
        sql.SQL(
            """
            INSERT INTO {} AS dataset_case (
                dataset_id,
                sample_id,
                case_id,
                source_item_id,
                variant,
                prompt_style,
                moral_domain,
                risk_track,
                scenario,
                initial_judgement,
                difficulty,
                difficulty_notes,
                source_file_id,
                source_line,
                raw_record,
                case_origin,
                is_canonical_dataset_item
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s::jsonb, 'inspect_eval_log_backfill', false)
            ON CONFLICT (dataset_id, sample_id) DO UPDATE SET
                case_id = COALESCE(dataset_case.case_id, EXCLUDED.case_id),
                source_item_id = COALESCE(dataset_case.source_item_id, EXCLUDED.source_item_id),
                variant = COALESCE(dataset_case.variant, EXCLUDED.variant),
                prompt_style = COALESCE(dataset_case.prompt_style, EXCLUDED.prompt_style),
                moral_domain = COALESCE(dataset_case.moral_domain, EXCLUDED.moral_domain),
                risk_track = COALESCE(dataset_case.risk_track, EXCLUDED.risk_track),
                scenario = COALESCE(dataset_case.scenario, EXCLUDED.scenario),
                initial_judgement = COALESCE(dataset_case.initial_judgement, EXCLUDED.initial_judgement),
                difficulty = COALESCE(dataset_case.difficulty, EXCLUDED.difficulty),
                difficulty_notes = COALESCE(dataset_case.difficulty_notes, EXCLUDED.difficulty_notes),
                source_file_id = COALESCE(dataset_case.source_file_id, EXCLUDED.source_file_id),
                raw_record = COALESCE(dataset_case.raw_record, '{{}}'::jsonb) || EXCLUDED.raw_record,
                updated_at = now()
            RETURNING case_pk
            """
        ).format(raw_relation("dataset_case")),
        (
            dataset_id,
            record["sample_id"],
            record["case_id"],
            record["source_item_id"],
            record["variant"],
            record["prompt_style"],
            record["moral_domain"],
            record["risk_track"],
            record["scenario"],
            record["initial_judgement"],
            record["difficulty"],
            record["difficulty_notes"],
            source_file_id,
            json_text(record["raw_record"]),
        ),
    )
    return int(cur.fetchone()["case_pk"])


def raw_case_intervention(cur, case_pk: int, metadata: dict[str, Any]) -> None:
    from psycopg import sql
    from postgres_schema_config import raw_relation

    useful_keys = (
        "user_followup",
        "evidence_quality",
        "pressure_type",
        "followup_strength",
        "expected_update",
        "pressure_source",
        "pressure_mechanism",
        "pressure_legitimacy",
        "pressure_escalation_stage",
        "pressure_target",
        "conflict_type",
        "safeguard_relevance_to_original_concern",
        "pressure_turns",
    )
    if not any(clean(metadata.get(key)) for key in useful_keys):
        return
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {} AS case_intervention (
                case_pk,
                user_followup,
                evidence_quality,
                pressure_type,
                followup_strength,
                expected_update,
                pressure_source,
                pressure_mechanism,
                pressure_legitimacy,
                pressure_escalation_stage,
                pressure_target,
                conflict_type,
                safeguard_relevance_to_original_concern,
                safeguard_type,
                safeguard_features,
                missing_safeguards,
                residual_risk_features,
                raw_metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
            ON CONFLICT (case_pk) DO UPDATE SET
                user_followup = COALESCE(case_intervention.user_followup, EXCLUDED.user_followup),
                evidence_quality = COALESCE(case_intervention.evidence_quality, EXCLUDED.evidence_quality),
                pressure_type = COALESCE(case_intervention.pressure_type, EXCLUDED.pressure_type),
                followup_strength = COALESCE(case_intervention.followup_strength, EXCLUDED.followup_strength),
                expected_update = COALESCE(case_intervention.expected_update, EXCLUDED.expected_update),
                raw_metadata = COALESCE(case_intervention.raw_metadata, '{{}}'::jsonb) || EXCLUDED.raw_metadata
            """
        ).format(raw_relation("case_intervention")),
        (
            case_pk,
            clean(metadata.get("user_followup")),
            clean(metadata.get("evidence_quality")),
            clean(metadata.get("pressure_type")),
            clean(metadata.get("followup_strength")),
            clean(metadata.get("expected_update")),
            clean(metadata.get("pressure_source")),
            clean(metadata.get("pressure_mechanism")),
            clean(metadata.get("pressure_legitimacy")),
            clean(metadata.get("pressure_escalation_stage")),
            clean(metadata.get("pressure_target")),
            clean(metadata.get("conflict_type")),
            clean(metadata.get("safeguard_relevance_to_original_concern")),
            json_text(jsonish(metadata.get("safeguard_type"), [])),
            json_text(jsonish(metadata.get("safeguard_features"), [])),
            json_text(jsonish(metadata.get("missing_safeguards"), [])),
            json_text(jsonish(metadata.get("residual_risk_features"), [])),
            json_text({"backfill_source": "inspect_eval_log", "inspect_metadata": metadata}),
        ),
    )


def raw_expected_behaviour(cur, case_pk: int, metadata: dict[str, Any]) -> None:
    from psycopg import sql
    from postgres_schema_config import raw_relation

    useful_keys = (
        "expected_behaviour",
        "ideal_behaviour",
        "target_scope",
        "maximum_acceptable_scope",
        "target_release_scope",
        "maximum_acceptable_release_scope",
        "target_access",
        "missing_evaluation_types",
        "failure_modes",
        "pressure_trap",
    )
    if not any(metadata.get(key) not in (None, "", [], {}) for key in useful_keys):
        return
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {} AS expected_behaviour (
                case_pk,
                expected_behaviour,
                ideal_behaviour,
                target_scope,
                minimum_acceptable_scope,
                maximum_acceptable_scope,
                target_release_scope,
                minimum_acceptable_release_scope,
                maximum_acceptable_release_scope,
                target_access,
                minimum_acceptable_access,
                maximum_acceptable_access,
                release_artifact,
                missing_evaluation_types,
                failure_modes,
                too_conservative_failure,
                too_permissive_failure,
                pressure_trap,
                raw_metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s::jsonb)
            ON CONFLICT (case_pk) DO UPDATE SET
                expected_behaviour = COALESCE(expected_behaviour.expected_behaviour, EXCLUDED.expected_behaviour),
                ideal_behaviour = COALESCE(expected_behaviour.ideal_behaviour, EXCLUDED.ideal_behaviour),
                target_scope = COALESCE(expected_behaviour.target_scope, EXCLUDED.target_scope),
                maximum_acceptable_scope = COALESCE(expected_behaviour.maximum_acceptable_scope, EXCLUDED.maximum_acceptable_scope),
                target_release_scope = COALESCE(expected_behaviour.target_release_scope, EXCLUDED.target_release_scope),
                maximum_acceptable_release_scope = COALESCE(expected_behaviour.maximum_acceptable_release_scope, EXCLUDED.maximum_acceptable_release_scope),
                raw_metadata = COALESCE(expected_behaviour.raw_metadata, '{{}}'::jsonb) || EXCLUDED.raw_metadata
            """
        ).format(raw_relation("expected_behaviour")),
        (
            case_pk,
            clean(metadata.get("expected_behaviour")),
            clean(metadata.get("ideal_behaviour")),
            clean(metadata.get("target_scope")),
            clean(metadata.get("minimum_acceptable_scope")),
            clean(metadata.get("maximum_acceptable_scope")),
            clean(metadata.get("target_release_scope")),
            clean(metadata.get("minimum_acceptable_release_scope")),
            clean(metadata.get("maximum_acceptable_release_scope")),
            json_text(jsonish(metadata.get("target_access"), {})),
            json_text(jsonish(metadata.get("minimum_acceptable_access"), {})),
            json_text(jsonish(metadata.get("maximum_acceptable_access"), {})),
            clean(metadata.get("release_artifact")),
            json_text(jsonish(metadata.get("missing_evaluation_types"), [])),
            json_text(jsonish(metadata.get("failure_modes"), [])),
            clean(metadata.get("too_conservative_failure")),
            clean(metadata.get("too_permissive_failure")),
            clean(metadata.get("pressure_trap")),
            json_text({"backfill_source": "inspect_eval_log", "inspect_metadata": metadata}),
        ),
    )


def raw_model_run(cur, summary: dict[str, Any], source_file_id: int) -> int:
    from psycopg import sql
    from postgres_schema_config import raw_relation

    path = summary["path"]
    model_name = clean(summary.get("model_name"))
    raw_metadata = {
        "backfill_source": "inspect_eval_log",
        "source_log_path": repo_display_path(path),
        "source_log_sha256": summary["sha256"],
        "task": summary.get("task"),
        "sample_count": summary.get("sample_count"),
        "inspect_eval": to_jsonable(get_attr_or_key(summary.get("log"), "eval", None)),
        "inspect_status": summary.get("status"),
    }
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {} AS model_run (
                run_label,
                model_name,
                provider,
                dataset_version,
                prompt_style,
                task_name,
                run_timestamp,
                source_file_id,
                raw_metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (run_label) DO UPDATE SET
                model_name = COALESCE(EXCLUDED.model_name, model_run.model_name),
                provider = COALESCE(EXCLUDED.provider, model_run.provider),
                dataset_version = COALESCE(EXCLUDED.dataset_version, model_run.dataset_version),
                prompt_style = COALESCE(EXCLUDED.prompt_style, model_run.prompt_style),
                task_name = COALESCE(EXCLUDED.task_name, model_run.task_name),
                run_timestamp = COALESCE(EXCLUDED.run_timestamp, model_run.run_timestamp),
                source_file_id = EXCLUDED.source_file_id,
                raw_metadata = COALESCE(model_run.raw_metadata, '{{}}'::jsonb) || EXCLUDED.raw_metadata,
                updated_at = now()
            RETURNING run_id
            """
        ).format(raw_relation("model_run")),
        (
            repo_stem_label(path),
            model_name,
            provider_from_model(model_name),
            clean(summary.get("dataset_version")),
            clean(summary.get("prompt_style")),
            clean(summary.get("task")),
            summary.get("run_timestamp"),
            source_file_id,
            json_text(raw_metadata),
        ),
    )
    return int(cur.fetchone()["run_id"])


def raw_model_response(
    cur,
    run_id: int,
    case_pk: int,
    source_file_id: int,
    source_row: int,
    sample: Any,
    summary: dict[str, Any],
) -> int:
    from psycopg import sql
    from postgres_schema_config import raw_relation

    metadata = sample_metadata(sample)
    sample_id = sample_identifier(sample, source_row) or f"sample-{source_row}"
    raw_row = {
        "backfill_source": "inspect_eval_log",
        "source_log_path": repo_display_path(summary["path"]),
        "source_log_sha256": summary["sha256"],
        "inspect_sample": to_jsonable(sample),
    }
    cur.execute(
        sql.SQL(
            """
            INSERT INTO {} AS model_response (
                run_id,
                case_pk,
                sample_id,
                case_id,
                source_item_id,
                dataset_version,
                prompt_style,
                raw_response,
                source_file_id,
                source_row,
                raw_row
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (run_id, sample_id) DO UPDATE SET
                case_pk = COALESCE(EXCLUDED.case_pk, model_response.case_pk),
                case_id = COALESCE(EXCLUDED.case_id, model_response.case_id),
                source_item_id = COALESCE(EXCLUDED.source_item_id, model_response.source_item_id),
                dataset_version = COALESCE(EXCLUDED.dataset_version, model_response.dataset_version),
                prompt_style = COALESCE(EXCLUDED.prompt_style, model_response.prompt_style),
                raw_response = COALESCE(EXCLUDED.raw_response, model_response.raw_response),
                source_file_id = EXCLUDED.source_file_id,
                source_row = EXCLUDED.source_row,
                raw_row = COALESCE(model_response.raw_row, '{{}}'::jsonb) || EXCLUDED.raw_row,
                updated_at = now()
            RETURNING response_id
            """
        ).format(raw_relation("model_response")),
        (
            run_id,
            case_pk,
            sample_id,
            clean(metadata.get("case_id")),
            clean(metadata.get("source_item_id")),
            sample_dataset_version(sample, summary),
            clean(metadata.get("prompt_style") or summary.get("prompt_style")),
            clean(extract_completion(sample)),
            source_file_id,
            source_row,
            json_text(raw_row),
        ),
    )
    return int(cur.fetchone()["response_id"])


def promote_operational_log(cur, summary: dict[str, Any]) -> dict[str, int]:
    source_file_id = source_file(cur, summary)
    dataset_version = clean(summary.get("dataset_version"))
    if not dataset_version:
        return {"promoted_runs": 0, "promoted_samples": 0, "skipped_samples": summary["sample_count"]}

    promoted_samples = 0
    skipped_samples = 0
    dataset_id = raw_dataset(cur, dataset_version, source_file_id)
    run_id: int | None = None
    for ordinal, sample in enumerate(summary["samples"], 1):
        promotable, _reason = is_promotable_sample(sample, summary, ordinal)
        if not promotable:
            skipped_samples += 1
            continue
        if run_id is None:
            run_id = raw_model_run(cur, summary, source_file_id)
        record = case_record_from_sample(sample, summary, ordinal)
        case_pk = raw_dataset_case(cur, dataset_id, source_file_id, record)
        metadata = sample_metadata(sample)
        raw_case_intervention(cur, case_pk, metadata)
        raw_expected_behaviour(cur, case_pk, metadata)
        raw_model_response(cur, run_id, case_pk, source_file_id, ordinal, sample, summary)
        promoted_samples += 1
    return {
        "promoted_runs": 1 if run_id is not None else 0,
        "promoted_samples": promoted_samples,
        "skipped_samples": skipped_samples,
    }


def promotion_dry_run(summary: dict[str, Any]) -> dict[str, Any]:
    reasons: Counter[str] = Counter()
    promotable = 0
    for ordinal, sample in enumerate(summary["samples"], 1):
        ok, reason = is_promotable_sample(sample, summary, ordinal)
        if ok:
            promotable += 1
        else:
            reasons[reason or "unknown"] += 1
    return {
        "path": repo_display_path(summary["path"]),
        "dataset_version": summary["dataset_version"],
        "sample_count": summary["sample_count"],
        "promotable_samples": promotable,
        "skip_reasons": dict(reasons),
    }


def choose_canonical_duplicate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def rank(row: dict[str, Any]) -> tuple[int, int, int]:
        key = str(row.get("pipeline_run_key") or "")
        is_backfill = key.startswith("inspect-log-backfill-")
        has_manifest = row.get("experiment_manifest_id") is not None
        return (1 if is_backfill else 0, 0 if has_manifest else 1, int(row["experiment_pipeline_run_id"]))

    return sorted(rows, key=rank)[0]


def duplicate_groups(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        WITH duplicate_sha AS (
            SELECT eval_log_sha256
            FROM public.experiment_pipeline_run
            WHERE eval_log_sha256 IS NOT NULL
            GROUP BY eval_log_sha256
            HAVING count(*) > 1
        )
        SELECT
            epr.experiment_pipeline_run_id,
            epr.experiment_manifest_id,
            epr.pipeline_run_key,
            epr.eval_log_sha256,
            epr.eval_log_path,
            epr.original_eval_log_path,
            epr.raw_run_metadata,
            count(ils.inspect_log_sample_id) AS sample_count
        FROM public.experiment_pipeline_run epr
        JOIN duplicate_sha dup
          ON dup.eval_log_sha256 = epr.eval_log_sha256
        LEFT JOIN public.inspect_log_sample ils
          ON ils.experiment_pipeline_run_id = epr.experiment_pipeline_run_id
        GROUP BY
            epr.experiment_pipeline_run_id,
            epr.experiment_manifest_id,
            epr.pipeline_run_key,
            epr.eval_log_sha256,
            epr.eval_log_path,
            epr.original_eval_log_path,
            epr.raw_run_metadata
        ORDER BY epr.eval_log_sha256, epr.experiment_pipeline_run_id
        """
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        grouped.setdefault(str(row["eval_log_sha256"]), []).append(dict(row))
    return [{"sha256": sha, "rows": rows, "canonical": choose_canonical_duplicate(rows)} for sha, rows in grouped.items()]


def durable_path_for_group(rows: list[dict[str, Any]]) -> str | None:
    paths: list[str] = []
    for row in rows:
        for key in ("original_eval_log_path", "eval_log_path"):
            path = row.get(key)
            if path:
                paths.append(str(path).replace("\\", "/"))
    for path in paths:
        if path.startswith("logs/") or "/logs/" in path:
            return path
    return paths[0] if paths else None


def apply_dedupe(cur, dry_run: bool = False) -> dict[str, Any]:
    groups = duplicate_groups(cur)
    report = {
        "duplicate_sha_groups": len(groups),
        "deleted_pipeline_runs": 0,
        "deleted_samples": 0,
        "reassigned_samples": 0,
        "skipped_pipeline_runs": 0,
        "groups": [],
    }
    for group in groups:
        canonical = group["canonical"]
        canonical_id = int(canonical["experiment_pipeline_run_id"])
        durable_path = durable_path_for_group(group["rows"])
        group_report = {
            "sha256": group["sha256"],
            "canonical_pipeline_run_id": canonical_id,
            "durable_log_path": durable_path,
            "duplicates": [],
        }
        if not dry_run and durable_path:
            cur.execute(
                """
                UPDATE public.experiment_pipeline_run
                SET
                    original_eval_log_path = COALESCE(original_eval_log_path, %s),
                    raw_run_metadata = COALESCE(raw_run_metadata, '{}'::jsonb) || %s::jsonb,
                    updated_at = now()
                WHERE experiment_pipeline_run_id = %s
                """,
                (
                    durable_path,
                    json_text({"dedupe": {"canonical_durable_log_path": durable_path}}),
                    canonical_id,
                ),
            )

        for row in group["rows"]:
            duplicate_id = int(row["experiment_pipeline_run_id"])
            if duplicate_id == canonical_id:
                continue
            duplicate_report = {"experiment_pipeline_run_id": duplicate_id}
            if not dry_run:
                cur.execute(
                    """
                    UPDATE public.inspect_log_sample sample
                    SET
                        experiment_pipeline_run_id = %s,
                        updated_at = now()
                    WHERE sample.experiment_pipeline_run_id = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM public.inspect_log_sample existing
                          WHERE existing.experiment_pipeline_run_id = %s
                            AND existing.sample_id = sample.sample_id
                      )
                    """,
                    (canonical_id, duplicate_id, canonical_id),
                )
                reassigned = cur.rowcount
                cur.execute(
                    """
                    DELETE FROM public.inspect_log_sample duplicate_sample
                    USING public.inspect_log_sample canonical_sample
                    WHERE duplicate_sample.experiment_pipeline_run_id = %s
                      AND canonical_sample.experiment_pipeline_run_id = %s
                      AND canonical_sample.sample_id = duplicate_sample.sample_id
                    """,
                    (duplicate_id, canonical_id),
                )
                deleted_samples = cur.rowcount
                cur.execute(
                    """
                    SELECT count(*) AS remaining
                    FROM public.inspect_log_sample
                    WHERE experiment_pipeline_run_id = %s
                    """,
                    (duplicate_id,),
                )
                remaining = int(cur.fetchone()["remaining"])
                if remaining == 0:
                    manifest_id = row.get("experiment_manifest_id")
                    cur.execute(
                        "DELETE FROM public.experiment_pipeline_run WHERE experiment_pipeline_run_id = %s",
                        (duplicate_id,),
                    )
                    deleted_runs = cur.rowcount
                    if manifest_id is not None:
                        cur.execute(
                            """
                            DELETE FROM public.experiment_manifest manifest
                            WHERE manifest.experiment_manifest_id = %s
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM public.experiment_pipeline_run epr
                                  WHERE epr.experiment_manifest_id = manifest.experiment_manifest_id
                              )
                              AND manifest.raw_manifest->>'backfill_source' = 'inspect_eval_log'
                            """,
                            (manifest_id,),
                        )
                    report["deleted_pipeline_runs"] += deleted_runs
                else:
                    report["skipped_pipeline_runs"] += 1
                report["reassigned_samples"] += reassigned
                report["deleted_samples"] += deleted_samples
                duplicate_report.update(
                    {
                        "reassigned_samples": reassigned,
                        "deleted_samples": deleted_samples,
                        "remaining_samples": remaining,
                    }
                )
            group_report["duplicates"].append(duplicate_report)
        report["groups"].append(group_report)
    return report


def linkage_counts(cur) -> dict[str, int]:
    cur.execute(
        """
        SELECT
            count(1) AS pipeline_runs,
            count(operational_run_id) AS linked_pipeline_runs
        FROM public.experiment_pipeline_run
        """
    )
    pipeline = dict(cur.fetchone())
    cur.execute(
        """
        SELECT
            count(1) AS inspect_samples,
            count(eval_case_id) AS linked_eval_cases,
            count(response_id) AS linked_responses
        FROM public.inspect_log_sample
        """
    )
    samples = dict(cur.fetchone())
    return {**pipeline, **samples}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill Inspect .eval logs into public pipeline/sample and operational tables.")
    parser.add_argument(
        "--log-dir",
        action="append",
        type=Path,
        default=None,
        help="Directory or .eval file to scan. Defaults to logs/.",
    )
    parser.add_argument("--include-tmp", action="store_true", help="Also scan tmp/ for .eval files.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of logs to process.")
    parser.add_argument("--write", action="store_true", help="Write public provenance rows to Postgres.")
    parser.add_argument("--promote-operational", action="store_true", help="Populate raw operational tables from promotable Inspect log samples and rebuild public operational tables.")
    parser.add_argument("--dedupe", action="store_true", help="Deduplicate public pipeline/sample rows that point at the same eval_log_sha256.")
    parser.add_argument("--diagnostics", action="store_true", help="Backfill passive response/model-call diagnostics for written Inspect log samples.")
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without writing to Postgres.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on the first unreadable log.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = args.log_dir or [Path("logs")]
    if args.include_tmp:
        paths = [*paths, Path("tmp")]
    logs = discover_eval_logs(paths)
    if args.limit is not None:
        logs = logs[: args.limit]

    print(f"Discovered .eval files: {len(logs)}")
    if not logs:
        print("No .eval files were visible under the requested path(s).")
        return 0

    summaries: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for path in logs:
        try:
            summary = summarise_log(path)
            summaries.append(summary)
            print(
                json.dumps(
                    {
                        "path": repo_display_path(path),
                        "sha256": summary["sha256"],
                        "dataset_version": summary["dataset_version"],
                        "prompt_style": summary["prompt_style"],
                        "model_name": summary["model_name"],
                        "task": summary["task"],
                        "sample_count": summary["sample_count"],
                    },
                    ensure_ascii=False,
                    default=str,
                )
            )
        except Exception as exc:
            errors.append({"path": repo_display_path(path), "error": str(exc)})
            print(json.dumps(errors[-1], ensure_ascii=False), file=sys.stderr)
            if args.fail_fast:
                raise

    dry_run = args.dry_run or not (args.write or args.promote_operational or args.dedupe)
    promotion_plan = [promotion_dry_run(summary) for summary in summaries]
    diagnostics_config = normalise_diagnostics_config(None)
    diagnostics_summary = empty_diagnostics_summary(
        requested=bool(args.diagnostics),
        dry_run=bool(args.diagnostics and (diagnostics_dry_run(args) or dry_run)),
    )

    if dry_run and not args.dedupe:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    **diagnostics_summary,
                    "diagnostics_note": (
                        "Diagnostics were requested but will only be populated when --write or "
                        "--promote-operational is supplied."
                        if args.diagnostics
                        else None
                    ),
                    "promotion_plan": promotion_plan,
                    "errors": errors,
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
        return 0 if not errors else 1

    with connect_db() as conn, conn.cursor() as cur:
        ensure_tables_and_linker(cur)
        dedupe_report = None
        written_logs = 0
        written_samples = 0
        promoted_runs = 0
        promoted_samples = 0
        skipped_promotion_samples = 0
        diagnostic_run_ids: list[int] = []
        if args.write or args.promote_operational:
            if diagnostics_write_enabled(args) and not dry_run:
                ensure_diagnostics_tables(cur)
            for summary in summaries:
                if args.write or args.promote_operational:
                    manifest_id = upsert_backfill_manifest(cur, summary)
                    pipeline_run_id = upsert_pipeline_run(
                        cur,
                        summary,
                        manifest_id,
                        diagnostics_config if args.diagnostics else None,
                    )
                    diagnostic_run_ids.append(pipeline_run_id)
                    written_logs += 1
                    written_samples += insert_samples(cur, pipeline_run_id, summary)
                if args.promote_operational:
                    result = promote_operational_log(cur, summary)
                    promoted_runs += result["promoted_runs"]
                    promoted_samples += result["promoted_samples"]
                    skipped_promotion_samples += result["skipped_samples"]
            if args.promote_operational:
                execute_derived_rebuild(cur)
            else:
                cur.execute(PIPELINE_LINK_SQL.read_text(encoding="utf-8"))
        if args.dedupe:
            dedupe_report = apply_dedupe(cur, dry_run=dry_run)
            cur.execute(PIPELINE_LINK_SQL.read_text(encoding="utf-8"))
        counts = linkage_counts(cur)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    diagnostics_summary["inspect_samples_inserted_or_updated"] = written_samples
    if diagnostics_write_enabled(args) and not dry_run:
        diagnostics_summary.update(run_diagnostics_backfill(diagnostic_run_ids, diagnostics_config))

    print(
        json.dumps(
            {
                "dry_run": dry_run,
                **diagnostics_summary,
                "written_logs": written_logs,
                "written_samples": written_samples,
                "promoted_runs": promoted_runs,
                "promoted_samples": promoted_samples,
                "skipped_promotion_samples": skipped_promotion_samples,
                "promotion_plan": promotion_plan,
                "dedupe_report": dedupe_report,
                "errors": errors,
                "linkage_counts": counts,
            },
            ensure_ascii=False,
            default=str,
            indent=2,
        )
    )
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
