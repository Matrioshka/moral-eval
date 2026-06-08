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
    get_run_sample,
    list_experiment_manifests,
    list_pipeline_runs,
    list_reference_rows,
    list_reference_tables,
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


templates.env.filters["json_pretty"] = json_pretty
templates.env.filters["preview_text"] = preview_text
templates.env.filters["model_label"] = model_label
templates.env.filters["message_role_label"] = message_role_label


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/runs")


@app.get("/runs")
def runs(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="runs.html",
        context={
            "runs": list_pipeline_runs(limit=50),
        },
    )


@app.get("/experiments")
def experiments(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="experiments.html",
        context={
            "experiments": list_experiment_manifests(),
        },
    )


@app.get("/experiments/{experiment_slug}")
def experiment_detail(request: Request, experiment_slug: str):
    experiment = get_experiment_manifest(experiment_slug)
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found.")
    return templates.TemplateResponse(
        request=request,
        name="experiment_detail.html",
        context={
            "experiment": experiment,
            "runs": list_runs_for_experiment(experiment_slug),
        },
    )


@app.get("/search")
def search(request: Request, q: str = Query(default="")):
    return templates.TemplateResponse(
        request=request,
        name="search.html",
        context={
            "q": q,
            "results": search_browser(q),
        },
    )


@app.get("/reference")
def reference(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="reference.html",
        context={
            "tables": list_reference_tables(),
        },
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
        context={
            "table_name": table_name,
            "rows": rows,
            "columns": reference_columns(rows),
        },
    )


@app.get("/runs/{run_id}")
def run_detail(request: Request, run_id: int):
    run = get_pipeline_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found.")
    return templates.TemplateResponse(
        request=request,
        name="run_detail.html",
        context={
            "run": run,
            "samples": list_run_samples(run_id),
        },
    )


@app.get("/runs/{run_id}/samples/{sample_id}")
def sample_detail(request: Request, run_id: int, sample_id: str):
    sample = get_run_sample(run_id, sample_id)
    if not sample:
        raise HTTPException(status_code=404, detail="Run sample not found.")
    return templates.TemplateResponse(
        request=request,
        name="sample_detail.html",
        context={
            "run_id": run_id,
            "sample": sample,
            "transcript": transcript_cards(sample),
        },
    )
