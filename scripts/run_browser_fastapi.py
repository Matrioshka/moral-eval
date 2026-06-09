# uvicorn scripts.run_browser_fastapi:app --reload
from __future__ import annotations

import json
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
    list_runs as list_model_runs,
    list_runs_for_experiment,
    list_run_samples,
    search_browser,
)


app = FastAPI(title="Moral Sycophancy Eval Run Browser")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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


def basename(value: Any) -> str:
    if not value:
        return ""
    return str(value).replace("\\", "/").rstrip("/").split("/")[-1]


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
templates.env.filters["model_label"] = model_label
templates.env.filters["message_role_label"] = message_role_label
templates.env.filters["basename"] = basename
templates.env.filters["status_kind"] = status_kind
templates.env.filters["status_label"] = status_label
templates.env.filters["task_label"] = task_label


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
