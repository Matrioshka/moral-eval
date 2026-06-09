# uvicorn scripts.run_browser_fastapi:app --reload
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scripts.app_queries import (
    get_experiment_manifest,
    get_pipeline_run,
    get_run_detail,
    get_run_sample,
    get_run_summary,
    list_experiment_manifests,
    list_pipeline_runs,
    list_reference_rows,
    list_reference_tables,
    list_reporting_view_rows,
    list_reporting_views,
    list_runs as list_model_runs,
    list_runs_for_experiment,
    list_run_samples,
    search_browser,
)


app = FastAPI(title="Moral Sycophancy Eval Run Browser")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_EVAL_LOG_DISPLAY_LIMIT_BYTES = 2 * 1024 * 1024


def json_pretty(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def preview_text(value: Any, limit: int = 420) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json_pretty(value)
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_datetime(value: Any) -> str:
    """Render datetimes compactly for browser pages.

    The DB stores UTC timestamps with a +00:00 suffix. For this local browser the
    suffix is noisy, so the display layer strips it everywhere by default while
    leaving the underlying values unchanged.
    """

    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        text = value.isoformat(sep=" ")
    else:
        text = str(value)
    if text.endswith("+00:00"):
        text = text[:-6]
    if text.endswith("Z"):
        text = text[:-1]
    return text


def basename(value: Any) -> str:
    if not value:
        return ""
    return str(value).replace("\\", "/").rstrip("/").split("/")[-1]


def inspect_view_command(value: Any) -> str:
    if not value:
        return ""
    path = str(value)
    if any(char.isspace() for char in path):
        path = f'"{path}"'
    return f"inspect view {path}"


def safe_eval_log_path(value: Any) -> Path:
    if not value:
        raise HTTPException(status_code=404, detail="Pipeline run has no eval log path.")

    raw_path = Path(str(value))
    candidate = raw_path if raw_path.is_absolute() else PROJECT_ROOT / raw_path
    resolved = candidate.resolve()
    project_root = PROJECT_ROOT.resolve()
    allowed_roots = ((project_root / "tmp").resolve(), (project_root / "logs").resolve())

    if not resolved.is_relative_to(project_root) or not any(resolved.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(status_code=403, detail="Eval log path is outside the allowed project log directories.")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(status_code=404, detail="Eval log file not found.")
    return resolved


def status_kind(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"ok", "success", "succeeded", "complete", "completed", "passed"}:
        return "ok"
    if status in {"failed", "failure", "error", "errored"}:
        return "error"
    if status in {"running", "in_progress", "in-progress", "started", "pending"}:
        return "running"
    if status in {"partial", "warning", "warn", "completed_with_warnings"}:
        return "warning"
    return "unknown"


def status_label(value: Any) -> str:
    label = str(value or "unknown")
    return f"\u2713 {label}" if status_kind(value) == "ok" else label


def task_label(value: Any, limit: int = 52) -> str:
    return preview_text(value, limit)


def human_label(value: Any) -> str:
    return str(value or "").replace("_", " ").strip().capitalize()


def is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def kv_rows(value: Any, prefix: str = "", max_depth: int = 5) -> list[dict[str, str]]:
    """Flatten dict/list configuration into readable key/value rows."""

    rows: list[dict[str, str]] = []

    def add_row(key: str, item: Any) -> None:
        if item is None:
            display_value = ""
        elif isinstance(item, bool):
            display_value = "true" if item else "false"
        else:
            display_value = str(item)
        rows.append({"key": key, "label": human_label(key.split(".")[-1]), "value": display_value})

    def walk(item: Any, path: str, depth: int) -> None:
        if depth > max_depth:
            add_row(path or "value", json_pretty(item))
            return
        if is_scalar(item):
            add_row(path or "value", item)
            return
        if isinstance(item, dict):
            if not item:
                add_row(path or "value", "{}")
                return
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                walk(child, child_path, depth + 1)
            return
        if isinstance(item, (list, tuple)):
            if not item:
                add_row(path or "items", "[]")
                return
            for index, child in enumerate(item, start=1):
                child_path = f"{path}[{index}]" if path else f"item[{index}]"
                walk(child, child_path, depth + 1)
            return
        add_row(path or "value", json_pretty(item))

    walk(value, prefix, 0)
    return rows


def model_label(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(label for label in (model_label(item) for item in value) if label)
    if isinstance(value, dict):
        return str(value.get("model") or value.get("name") or value.get("id") or "")
    return str(value)


def message_role_label(message: Any) -> str:
    if not isinstance(message, dict):
        return "message"
    role = str(message.get("role") or message.get("source") or message.get("type") or "message")
    model = model_label(message.get("model") or message.get("model_name"))
    return f"{role} - {model}" if model else role


def message_content(message: Any) -> Any:
    if not isinstance(message, dict):
        return message
    return message.get("content") or message.get("text") or message


def transcript_cards(sample: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = sample.get("metadata") if isinstance(sample.get("metadata"), dict) else {}
    pressure_turns = metadata.get("pressure_turns") if isinstance(metadata, dict) else []
    followups = {
        str(turn.get("user_followup", "")).strip(): turn
        for turn in pressure_turns
        if isinstance(turn, dict) and turn.get("user_followup")
    }

    cards: list[dict[str, Any]] = []
    for message in sample.get("messages") or []:
        content = message_content(message)
        pressure_turn = followups.get(content.strip()) if isinstance(content, str) else None
        role_key = str(message.get("role", "message")).lower() if isinstance(message, dict) else "message"
        cards.append(
            {
                "role": message_role_label(message),
                "role_key": role_key,
                "content": content,
                "pressure_turn": pressure_turn,
            }
        )
    return cards


def reference_columns(rows: list[dict[str, Any]]) -> list[str]:
    useful = (
        "id",
        "slug",
        "name",
        "description",
        "rubric_name",
        "score_scale",
        "pressure_family",
        "strength_rank",
        "scorer_type",
        "model_name",
    )
    if not rows:
        return []
    keys = set(rows[0])
    id_columns = [key for key in rows[0] if key.endswith("_id")]
    columns = [key for key in (*id_columns, *useful) if key in keys]
    return list(dict.fromkeys(columns))


def view_columns(view_name: str, rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []

    data_dictionary_columns = (
        "object_schema",
        "object_type",
        "parent_object_name",
        "object_name",
        "logical_name",
        "business_name",
        "data_domain",
        "subject_area",
        "data_type",
        "is_nullable",
        "is_primary_key",
        "is_foreign_key",
        "postgres_comment",
        "generated_definition",
        "review_status",
    )
    run_columns = (
        "run_id",
        "run_label",
        "model_name",
        "dataset_version",
        "response_count",
        "score_event_count",
        "run_timestamp",
        "response_id",
        "sample_id",
        "case_id",
        "turn_index",
        "response_text",
        "model_raw_response",
        "score",
        "label",
    )

    available = set(rows[0])
    preferred = data_dictionary_columns if "data_dictionary" in view_name else run_columns
    columns = [column for column in preferred if column in available]
    if columns:
        return columns
    return list(rows[0])[:14]


def sample_metadata_badges(sample: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = sample.get("metadata") if isinstance(sample.get("metadata"), dict) else {}
    categorical = {"moral_domain", "risk_track", "evidence_quality", "pressure_type", "expected_update", "difficulty"}
    fields = (
        ("dataset_version", sample.get("dataset_version")),
        ("case_id", metadata.get("case_id")),
        ("moral_domain", metadata.get("moral_domain")),
        ("risk_track", metadata.get("risk_track")),
        ("evidence_quality", metadata.get("evidence_quality")),
        ("pressure_type", metadata.get("pressure_type")),
        ("pressure_turn_count", metadata.get("pressure_turn_count")),
        ("expected_update", metadata.get("expected_update")),
        ("difficulty", metadata.get("difficulty")),
    )
    return [
        {"label": label, "value": value, "is_badge": label in categorical}
        for label, value in fields
        if value not in (None, "")
    ]


def with_active_nav(active_nav: str, **context: Any) -> dict[str, Any]:
    return {"active_nav": active_nav, **context}


templates.env.filters["json_pretty"] = json_pretty
templates.env.filters["preview_text"] = preview_text
templates.env.filters["format_datetime"] = format_datetime
templates.env.filters["model_label"] = model_label
templates.env.filters["message_role_label"] = message_role_label
templates.env.filters["basename"] = basename
templates.env.filters["inspect_view_command"] = inspect_view_command
templates.env.filters["status_kind"] = status_kind
templates.env.filters["status_label"] = status_label
templates.env.filters["task_label"] = task_label
templates.env.filters["human_label"] = human_label
templates.env.filters["kv_rows"] = kv_rows


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/runs")


@app.get("/runs")
def runs(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context=with_active_nav("runs", runs=list_model_runs(limit=50)),
    )


@app.get("/pipeline-runs")
def pipeline_runs(request: Request):
    runs = list_pipeline_runs(limit=50)
    return templates.TemplateResponse(
        request=request,
        name="pipeline_runs.html",
        context=with_active_nav(
            "pipeline_runs",
            runs=[
                {
                    **run,
                    "sample_count": len(list_run_samples(run["experiment_pipeline_run_id"])),
                }
                for run in runs
            ],
        ),
    )


@app.get("/experiments")
def experiments(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="experiments.html",
        context=with_active_nav(
            "experiments",
            experiments=list_experiment_manifests(),
        ),
    )


@app.get("/experiments/{experiment_slug}")
def experiment_detail(request: Request, experiment_slug: str):
    experiment = get_experiment_manifest(experiment_slug)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    return templates.TemplateResponse(
        request=request,
        name="experiment_detail.html",
        context=with_active_nav(
            "experiments",
            experiment=experiment,
            runs=list_runs_for_experiment(experiment_slug),
        ),
    )


@app.get("/search")
def search(request: Request, q: str = Query(default="")):
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context=with_active_nav(
            "search",
            q=q,
            results=search_browser(q),
        ),
    )


@app.get("/reference")
def reference(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="reference.html",
        context=with_active_nav(
            "reference",
            tables=list_reference_tables(),
        ),
    )


@app.get("/reference/{table_name}")
def reference_table(request: Request, table_name: str):
    available = {table["table_name"] for table in list_reference_tables()}
    if table_name not in available:
        raise HTTPException(status_code=404, detail="Reference table not found.")
    rows = list_reference_rows(table_name)
    return templates.TemplateResponse(
        request=request,
        name="reference_table.html",
        context=with_active_nav(
            "reference",
            table_name=table_name,
            rows=rows,
            columns=reference_columns(rows),
        ),
    )


@app.get("/views")
def views(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="views.html",
        context=with_active_nav(
            "views",
            views=list_reporting_views(),
        ),
    )


@app.get("/views/{view_name}")
def view_detail(request: Request, view_name: str):
    available = {view["view_name"]: view for view in list_reporting_views()}
    view = available.get(view_name)
    if view is None:
        raise HTTPException(status_code=404, detail="Reporting view not found.")
    rows = list_reporting_view_rows(view_name)
    return templates.TemplateResponse(
        request=request,
        name="view_detail.html",
        context=with_active_nav(
            "views",
            view=view,
            rows=rows,
            columns=view_columns(view_name, rows),
        ),
    )


@app.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int):
    run = get_run_summary(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Model/eval run not found.")
    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context=with_active_nav(
            "runs",
            run=run,
            rows=get_run_detail(run_id),
        ),
    )


@app.get("/pipeline-runs/{pipeline_run_id}")
def pipeline_run_detail(request: Request, pipeline_run_id: int):
    run = get_pipeline_run(pipeline_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found.")
    samples = list_run_samples(pipeline_run_id)
    return templates.TemplateResponse(
        request=request,
        name="pipeline_run_detail.html",
        context=with_active_nav(
            "pipeline_runs",
            run=run,
            samples=samples,
            sample_count=len(samples),
        ),
    )


@app.get("/pipeline-runs/{pipeline_run_id}/eval-log")
def pipeline_run_eval_log(request: Request, pipeline_run_id: int):
    run = get_pipeline_run(pipeline_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found.")
    if not run.get("eval_log_path"):
        raise HTTPException(status_code=404, detail="Pipeline run has no eval log path.")
    samples = list_run_samples(pipeline_run_id)
    return templates.TemplateResponse(
        request=request,
        name="eval_log.html",
        context=with_active_nav(
            "pipeline_runs",
            run=run,
            samples=samples,
            sample_count=len(samples),
        ),
    )


@app.get("/pipeline-runs/{pipeline_run_id}/eval-log/raw")
def pipeline_run_eval_log_raw(request: Request, pipeline_run_id: int):
    run = get_pipeline_run(pipeline_run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found.")
    eval_log_path = safe_eval_log_path(run.get("eval_log_path"))

    with eval_log_path.open("rb") as handle:
        content = handle.read(RAW_EVAL_LOG_DISPLAY_LIMIT_BYTES + 1)
    truncated = len(content) > RAW_EVAL_LOG_DISPLAY_LIMIT_BYTES
    if truncated:
        content = content[:RAW_EVAL_LOG_DISPLAY_LIMIT_BYTES]

    return templates.TemplateResponse(
        request=request,
        name="eval_log_raw.html",
        context=with_active_nav(
            "pipeline_runs",
            run=run,
            eval_log_path=eval_log_path,
            raw_text=content.decode("utf-8", errors="replace"),
            truncated=truncated,
            display_limit_mb=RAW_EVAL_LOG_DISPLAY_LIMIT_BYTES // (1024 * 1024),
        ),
    )


@app.get("/pipeline-runs/{pipeline_run_id}/samples/{sample_id}")
def sample_detail(request: Request, pipeline_run_id: int, sample_id: str):
    sample = get_run_sample(pipeline_run_id, sample_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Run sample not found.")
    return templates.TemplateResponse(
        request=request,
        name="sample_detail.html",
        context=with_active_nav(
            "pipeline_runs",
            pipeline_run_id=pipeline_run_id,
            sample=sample,
            metadata_badges=sample_metadata_badges(sample),
            transcript=transcript_cards(sample),
        ),
    )
