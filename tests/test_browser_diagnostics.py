from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table, create_engine, insert

import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import app_queries  # noqa: E402
import run_browser_fastapi  # noqa: E402


def fake_url_for(name: str, **params: object) -> str:
    if name == "static":
        return f"/static/{params.get('path', '')}"
    if name == "experiment_detail":
        return f"/experiments/{params['experiment_slug']}"
    if name == "pipeline_run_detail":
        return f"/pipeline-runs/{params['pipeline_run_id']}"
    if name == "pipeline_run_diagnostics":
        return f"/pipeline-runs/{params['pipeline_run_id']}/diagnostics"
    if name == "sample_detail":
        return f"/pipeline-runs/{params['pipeline_run_id']}/samples/{params['sample_id']}"
    if name == "search":
        return "/search"
    suffix = "/".join(str(value) for value in params.values())
    return f"/{name}/{suffix}".rstrip("/")


def render_template(name: str, **context: object) -> str:
    base_context = {
        "active_nav": "pipeline_runs",
        "q": "",
        "url_for": fake_url_for,
    }
    base_context.update(context)
    return run_browser_fastapi.templates.env.get_template(name).render(**base_context)


def test_model_call_diagnostics_summary_view_sql_exists() -> None:
    migration = (ROOT / "sql" / "022_create_model_call_diagnostics.sql").read_text(encoding="utf-8")

    assert "CREATE VIEW rpt.model_call_diagnostics_by_sample AS" in migration
    assert "total_input_tokens_provider_reported" in migration
    assert "total_provider_reported_total_tokens" in migration
    assert "has_unverified_linkage" in migration
    assert "DROP VIEW IF EXISTS rpt.model_call_diagnostics_by_sample;" in migration


def test_response_diagnostics_view_is_registered() -> None:
    assert "response_diagnostics" in app_queries.REPORTING_VIEW_NAMES
    assert "model_call_diagnostics" in app_queries.REPORTING_VIEW_NAMES
    assert "model_call_diagnostics_by_sample" in app_queries.REPORTING_VIEW_NAMES


def test_diagnostics_views_use_meaningful_display_columns() -> None:
    summary_columns = run_browser_fastapi.view_columns(
        "model_call_diagnostics_by_sample",
        [
            {
                "experiment_pipeline_run_id": 1,
                "inspect_log_sample_id": 2,
                "sample_id": "sample-1",
                "dataset_version": "v5",
                "model_name": "model",
                "model_call_count": 4,
                "exact_linked_count": 1,
                "unverified_count": 3,
                "total_provider_reported_total_tokens": 100,
                "all_headline_eligible": False,
                "has_unverified_linkage": True,
                "other": "ignored",
            }
        ],
    )
    row_columns = run_browser_fastapi.view_columns(
        "model_call_diagnostics",
        [
            {
                "experiment_pipeline_run_id": 1,
                "inspect_log_sample_id": 2,
                "sample_id": "sample-1",
                "model_call_index": 0,
                "turn_label": "baseline",
                "source_event_index": 5,
                "link_confidence": "unverified",
                "link_method": "raw_event_only",
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "reasoning_tokens": None,
                "thinking_tokens": 0,
                "headline_eligible": False,
            }
        ],
    )
    response_columns = run_browser_fastapi.view_columns(
        "response_diagnostics",
        [
            {
                "experiment_pipeline_run_id": 1,
                "inspect_log_sample_id": 2,
                "sample_id": "sample-1",
                "response_id": 9,
                "diagnostic_mode": "usage_only",
                "diagnostic_version": "v1",
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "reasoning_tokens": None,
                "thinking_tokens": 0,
                "headline_eligible": True,
            }
        ],
    )

    assert summary_columns == [
        "experiment_pipeline_run_id",
        "inspect_log_sample_id",
        "sample_id",
        "dataset_version",
        "model_name",
        "model_call_count",
        "exact_linked_count",
        "unverified_count",
        "total_provider_reported_total_tokens",
        "all_headline_eligible",
        "has_unverified_linkage",
    ]
    assert "link_confidence" in row_columns
    assert "headline_eligible" in row_columns
    assert response_columns[:6] == [
        "experiment_pipeline_run_id",
        "inspect_log_sample_id",
        "sample_id",
        "response_id",
        "diagnostic_mode",
        "diagnostic_version",
    ]


def test_view_detail_json_block_is_collapsed_by_default() -> None:
    html = render_template(
        "view_detail.html",
        view={
            "view_name": "model_call_diagnostics",
            "label": "Model-call diagnostics",
            "description": "Passive diagnostics.",
        },
        rows=[{"sample_id": "sample-1", "headline_eligible": False}],
        columns=["sample_id", "headline_eligible"],
    )

    assert "<summary>Displayed rows as JSON</summary>" in html
    assert "<details class=\"panel\">" in html
    assert "<details class=\"panel\" open" not in html


def test_model_call_query_helper_returns_diagnostics_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine("sqlite://")
    metadata = MetaData()
    model_call_diagnostic = Table(
        "model_call_diagnostic",
        metadata,
        Column("model_call_diagnostic_id", Integer, primary_key=True),
        Column("experiment_pipeline_run_id", Integer),
        Column("inspect_log_sample_id", Integer),
        Column("model_call_index", Integer),
        Column("source_event_index", Integer),
        Column("turn_label", String),
        Column("link_confidence", String),
        Column("headline_eligible", Boolean),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(model_call_diagnostic),
            [
                {
                    "model_call_diagnostic_id": 1,
                    "experiment_pipeline_run_id": 10,
                    "inspect_log_sample_id": 20,
                    "model_call_index": 0,
                    "source_event_index": 5,
                    "turn_label": "baseline",
                    "link_confidence": "unverified",
                    "headline_eligible": False,
                }
            ],
        )

    monkeypatch.setattr(app_queries, "_engine", lambda: engine)
    monkeypatch.setattr(app_queries, "_model_call_diagnostic", lambda: model_call_diagnostic)

    rows = app_queries.list_model_call_diagnostics_for_sample(20)

    assert len(rows) == 1
    assert rows[0]["turn_label"] == "baseline"
    assert rows[0]["link_confidence"] == "unverified"
    assert rows[0]["headline_eligible"] is False


def test_pipeline_run_diagnostics_template_renders_rows() -> None:
    html = render_template(
        "pipeline_run_diagnostics.html",
        run={
            "experiment_pipeline_run_id": 10,
            "experiment_slug": "moral-reasoning-integrity-behaviour",
        },
        model_call_summary=[
            {
                "sample_id": "sample-1",
                "model_call_count": 4,
                "exact_linked_count": 1,
                "unverified_count": 3,
                "has_unverified_linkage": True,
                "total_input_tokens_provider_reported": 100,
                "total_output_tokens_provider_reported": 20,
                "total_provider_reported_total_tokens": 130,
                "total_reasoning_tokens_provider_reported": 10,
                "total_thinking_tokens_provider_reported": 0,
                "all_headline_eligible": False,
            }
        ],
        response_diagnostics=[],
    )

    assert "Model-Call Diagnostics By Sample" in html
    assert "unverified/raw event only" in html
    assert "headline_eligible=false" in html
    assert "Provider-reported total tokens" in html


def test_pipeline_runs_template_does_not_link_missing_experiment_slug() -> None:
    html = render_template(
        "pipeline_runs.html",
        runs=[
            {
                "experiment_pipeline_run_id": 1,
                "experiment_slug": "inspect-log-backfill-v5-multistage-pressure-pilot-v0",
                "experiment_exists": False,
                "status": "imported",
                "dataset_version": "v5",
                "answer_model": {"model": "openrouter/openai/gpt-4.1"},
                "sample_count": 1,
                "started_at": None,
                "completed_at": None,
            }
        ],
    )

    assert "inspect-log-backfill-v5-multistage-pressure-pilot-v0" in html
    assert "/experiments/inspect-log-backfill-v5-multistage-pressure-pilot-v0" not in html
    assert "/pipeline-runs/1/diagnostics" in html
    assert "Diagnostics" in html
    assert 'class="button-link" href="/pipeline-runs/1/diagnostics"' not in html


def test_pipeline_runs_template_links_existing_experiment_slug() -> None:
    html = render_template(
        "pipeline_runs.html",
        runs=[
            {
                "experiment_pipeline_run_id": 2,
                "experiment_slug": "real-experiment",
                "experiment_exists": True,
                "status": "completed",
                "dataset_version": "v5",
                "answer_model": {"model": "openrouter/openai/gpt-4.1"},
                "sample_count": 1,
                "started_at": None,
                "completed_at": None,
            }
        ],
    )

    assert 'href="/experiments/real-experiment"' in html
    assert "/pipeline-runs/2/diagnostics" in html


def test_sample_detail_template_renders_diagnostics_when_present() -> None:
    html = render_template(
        "sample_detail.html",
        pipeline_run_id=10,
        sample={
            "sample_id": "sample-1",
            "inspect_log_sample_id": 20,
            "experiment_pipeline_run_id": 10,
            "dataset_version": "v5",
            "source_log_path": "logs/example.eval",
            "input_text": "input",
            "target_text": "target",
            "final_response": "answer",
            "metadata": {},
        },
        metadata_badges=[],
        transcript=[],
        model_call_diagnostics=[
            {
                "model_call_index": 0,
                "turn_label": "baseline",
                "source_event_index": 5,
                "link_confidence": "unverified",
                "link_method": "raw_event_only",
                "input_tokens": 1,
                "output_tokens": 2,
                "total_tokens": 3,
                "reasoning_tokens": None,
                "thinking_tokens": 0,
                "headline_eligible": False,
            }
        ],
        response_diagnostics=[],
    )

    assert "Model-Call Diagnostics" in html
    assert "model_call_diagnostic" in html
    assert "unverified/raw event only" in html
    assert "headline_eligible=false" in html
    assert "Provider-reported input tokens" in html


def test_sample_detail_template_renders_without_diagnostics() -> None:
    html = render_template(
        "sample_detail.html",
        pipeline_run_id=10,
        sample={
            "sample_id": "sample-1",
            "inspect_log_sample_id": 20,
            "experiment_pipeline_run_id": 10,
            "dataset_version": "v5",
            "source_log_path": "logs/example.eval",
            "input_text": "input",
            "target_text": "target",
            "final_response": "answer",
            "metadata": {},
        },
        metadata_badges=[],
        transcript=[],
        model_call_diagnostics=[],
        response_diagnostics=[],
    )

    assert "Inspect Log Sample sample-1" in html
    assert "Model-Call Diagnostics" not in html


def test_sample_detail_template_hides_duplicate_full_source_log_path() -> None:
    html = render_template(
        "sample_detail.html",
        pipeline_run_id=10,
        sample={
            "sample_id": "sample-1",
            "inspect_log_sample_id": 20,
            "experiment_pipeline_run_id": 10,
            "dataset_version": "v5",
            "source_log_path": "example.eval",
            "input_text": "input",
            "target_text": "target",
            "final_response": "answer",
            "metadata": {},
        },
        metadata_badges=[],
        transcript=[],
        model_call_diagnostics=[],
        response_diagnostics=[],
    )

    assert "Source log" in html
    assert "Full source log path" not in html
