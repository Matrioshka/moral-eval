#!/usr/bin/env python
"""
Generate a heatmap of success/failure modes by scenario.

Default assumption:
    Use rpt.case_run_trace_reporting, or another reporting view/table with:
      - one column identifying the scenario/case
      - one column identifying the success/failure mode

Example:
    python scripts/plot_failure_mode_heatmap.py ^
      --table rpt.case_run_trace_reporting ^
      --scenario-col scenario ^
      --mode-col failure_class ^
      --out tmp/failure_mode_heatmap.png

If unsure about column names:
    python scripts/plot_failure_mode_heatmap.py --table rpt.case_run_trace_reporting --list-columns
"""

from __future__ import annotations

import argparse
import os
import re
import textwrap
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
QUALIFIED_IDENTIFIER_RE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$"
)


SCENARIO_CANDIDATES = [
    "scenario_name",
    "scenario_title",
    "scenario",
    "scenario_text",
    "case_title",
    "case_id",
    "eval_case_id",
    "dataset_case_id",
    "sample_id",
]

MODE_CANDIDATES = [
    "failure_class",
    "failure_class_name",
    "outcome_class",
    "score_label",
    "label",
    "classification",
    "judgement_class",
    "rubric_label",
]

OUTCOME_ORDER_HINTS = [
    "Calibrated",
    "Miscalibrated",
    "Moral Sycophancy",
    "Model over",
    "Ignores",
    "Residual",
    "SCHEMA",
    "No failure",
]


def validate_identifier(value: str, *, qualified: bool = False) -> str:
    pattern = QUALIFIED_IDENTIFIER_RE if qualified else IDENTIFIER_RE
    if not pattern.match(value):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return value


def split_table_name(table: str) -> tuple[str, str]:
    validate_identifier(table, qualified=True)
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "public", table
    return schema, name


def quote_ident(identifier: str) -> str:
    validate_identifier(identifier)
    return f'"{identifier}"'


def quote_table(table: str) -> str:
    schema, name = split_table_name(table)
    return f'{quote_ident(schema)}.{quote_ident(name)}'


def sqlalchemy_dsn(value: str) -> str:
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value.removeprefix("postgresql://")
    return value


def get_columns(engine, table: str) -> list[str]:
    schema, name = split_table_name(table)

    query = text(
        """
        select column_name
        from information_schema.columns
        where table_schema = :schema
          and table_name = :name
        order by ordinal_position
        """
    )

    with engine.begin() as conn:
        rows = conn.execute(query, {"schema": schema, "name": name}).fetchall()

    return [row[0] for row in rows]


def normalise_where_sql(where_sql: str | None, columns: list[str]) -> str | None:
    if not where_sql:
        return None

    normalised = where_sql
    column_set = {column.lower() for column in columns}

    if "dataset_alias" not in column_set and "dataset_version" in column_set:
        normalised = re.sub(r"\bdataset_alias\b", 't."dataset_version"', normalised, flags=re.I)

    if "scorer_name" not in column_set and "manual_score" in column_set:
        normalised = re.sub(
            r"\bscorer_name\s*=\s*'manual'",
            't."manual_score" is not null',
            normalised,
            flags=re.I,
        )
        normalised = re.sub(
            r"'manual'\s*=\s*\bscorer_name\b",
            't."manual_score" is not null',
            normalised,
            flags=re.I,
        )

    return normalised


def choose_column(columns: list[str], explicit: str | None, candidates: list[str], label: str) -> str:
    if explicit:
        if explicit not in columns:
            raise ValueError(
                f"{label} column {explicit!r} not found. Available columns:\n"
                + "\n".join(f"  - {c}" for c in columns)
            )
        return explicit

    lower_map = {c.lower(): c for c in columns}
    for candidate in candidates:
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]

    raise ValueError(
        f"Could not auto-detect {label} column.\n"
        f"Pass --{label.replace(' ', '-')}-col explicitly.\n\n"
        "Available columns:\n" + "\n".join(f"  - {c}" for c in columns)
    )


def humanise_identifier(value: str) -> str:
    value = str(value).replace("\n", " ").strip()
    if not value:
        return value
    value = re.sub(r"^(scope_control|release_schema|frontier|mri|behaviour)[_-]+", "", value, flags=re.I)
    value = re.sub(r"[_-]+", " ", value)
    value = re.sub(r"\b(ai|api|gpt|pii|v2|v3|v4)\b", lambda m: m.group(1).upper(), value, flags=re.I)
    return value[:1].upper() + value[1:]


def title_case_mode(value: str) -> str:
    value = str(value).strip()
    if not value:
        return value
    if value == "none":
        return "No failure class"
    value = value.replace("_", " ").replace("-", " ")
    words = []
    for word in value.split():
        if word.upper() in {"AI", "API", "PII", "V2", "V2.1", "V3", "V4"}:
            words.append(word.upper())
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return " ".join(words)


def sorted_mode_columns(columns: list[str]) -> list[str]:
    def sort_key(col: str) -> tuple[int, str]:
        for idx, prefix in enumerate(OUTCOME_ORDER_HINTS):
            if col.lower().startswith(prefix.lower()):
                return idx, col.lower()
        return len(OUTCOME_ORDER_HINTS), col.lower()

    return sorted(columns, key=sort_key)


def add_unique_scenario_display_labels(counts: pd.DataFrame) -> pd.DataFrame:
    labels = counts[["scenario_key", "scenario_label"]].drop_duplicates()
    duplicated_labels = labels["scenario_label"].duplicated(keep=False)
    duplicate_names = set(labels.loc[duplicated_labels, "scenario_label"])

    labels["scenario_display"] = labels.apply(
        lambda row: (
            f"{row['scenario_label']} ({humanise_identifier(row['scenario_key'])})"
            if row["scenario_label"] in duplicate_names
            else row["scenario_label"]
        ),
        axis=1,
    )

    return counts.merge(labels[["scenario_key", "scenario_display"]], on="scenario_key", how="left")


def fetch_counts(
    engine,
    *,
    table: str,
    scenario_col: str,
    scenario_label_col: str | None,
    mode_col: str,
    where_sql: str | None,
    columns: list[str],
) -> pd.DataFrame:
    table_sql = quote_table(table)
    scenario_sql = quote_ident(scenario_col)
    mode_sql = quote_ident(mode_col)
    scenario_label_sql = quote_ident(scenario_label_col) if scenario_label_col else None

    has_case_pk = "case_pk" in columns
    use_scenario_join = has_case_pk and scenario_label_col is None

    where_sql = normalise_where_sql(where_sql, columns)
    where_clause = f"and ({where_sql})" if where_sql else ""
    join_clause = ""
    label_expr = f"cast(t.{scenario_sql} as text)"

    if use_scenario_join:
        join_clause = """
        left join public.eval_case __ec
          on __ec.dataset_case_pk = t."case_pk"
        left join public.scenario __scenario
          on __scenario.scenario_id = __ec.scenario_id
        """
        label_expr = f"coalesce(__scenario.name, cast(t.{scenario_sql} as text))"
    elif scenario_label_sql:
        label_expr = f"cast(t.{scenario_label_sql} as text)"

    query = f"""
        select
            cast(t.{scenario_sql} as text) as scenario_key,
            {label_expr} as scenario_label,
            cast(t.{mode_sql} as text) as mode,
            count(*)::int as n
        from {table_sql} t
        {join_clause}
        where t.{scenario_sql} is not null
          and t.{mode_sql} is not null
          and trim(cast(t.{scenario_sql} as text)) <> ''
          and trim(cast(t.{mode_sql} as text)) <> ''
          {where_clause}
        group by 1, 2, 3
        order by 1, 3
    """

    with engine.connect() as conn:
        counts = pd.read_sql_query(text(query), conn)
    counts["scenario_label"] = counts["scenario_label"].map(humanise_identifier)
    counts["mode"] = counts["mode"].map(title_case_mode)
    return counts


def empty_selection_diagnostics(
    engine,
    *,
    table: str,
    mode_col: str,
    columns: list[str],
) -> pd.DataFrame:
    table_sql = quote_table(table)
    mode_sql = quote_ident(mode_col)
    dataset_expr = 'cast(t."dataset_version" as text)' if "dataset_version" in columns else "'<unknown>'"
    manual_expr = 't."manual_score" is not null' if "manual_score" in columns else "false"

    query = f"""
        select
            {dataset_expr} as dataset_version,
            count(*)::int as rows,
            count(t.{mode_sql})::int as mode_values,
            count(*) filter (where {manual_expr})::int as manual_scores,
            count(*) filter (where {manual_expr} and t.{mode_sql} is not null)::int as manual_mode_values
        from {table_sql} t
        group by 1
        order by dataset_version nulls last
    """

    with engine.connect() as conn:
        return pd.read_sql_query(text(query), conn)


def shorten_label(value: str, width: int = 48) -> str:
    value = str(value).replace("\n", " ").strip()
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def wrap_label(value: str, width: int = 32) -> str:
    value = str(value).replace("\n", " ").strip()
    return "\n".join(textwrap.wrap(value, width=width, break_long_words=False))


def display_label(value: str, width: int = 52) -> str:
    value = str(value).replace("\n", " ").strip()
    if len(value) <= width:
        return value
    return value[: width - 3] + "..."


def display_wrapped_label(value: str, width: int = 28) -> str:
    value = str(value).replace("\n", " ").strip()
    return "\n".join(textwrap.wrap(value, width=width, break_long_words=False))


def build_matrix(
    counts: pd.DataFrame,
    *,
    top_n_scenarios: int | None,
    normalise: str | None,
) -> pd.DataFrame:
    if counts.empty:
        raise RuntimeError(
            "No rows found for the selected table/columns/filter. "
            "Check --where and --mode-col; the selected slice may have no scored failure-class rows."
        )

    counts = add_unique_scenario_display_labels(counts)

    matrix = counts.pivot_table(
        index="scenario_display",
        columns="mode",
        values="n",
        aggfunc="sum",
        fill_value=0,
    )

    matrix["__total__"] = matrix.sum(axis=1)
    matrix = matrix.sort_values("__total__", ascending=False)

    if top_n_scenarios is not None:
        matrix = matrix.head(top_n_scenarios)

    matrix = matrix.drop(columns=["__total__"])
    matrix = matrix[sorted_mode_columns(list(matrix.columns))]

    if normalise == "row":
        row_sums = matrix.sum(axis=1).replace(0, np.nan)
        matrix = matrix.div(row_sums, axis=0).fillna(0) * 100
    elif normalise == "column":
        col_sums = matrix.sum(axis=0).replace(0, np.nan)
        matrix = matrix.div(col_sums, axis=1).fillna(0) * 100
    elif normalise is not None:
        raise ValueError("--normalise must be one of: row, column")

    return matrix


def plot_heatmap(
    matrix: pd.DataFrame,
    *,
    out_path: Path,
    title: str,
    normalise: str | None,
    annotate: bool,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_rows, n_cols = matrix.shape

    # Scale figure size to keep labels readable.
    fig_width = max(12, min(26, 4.0 + n_cols * 1.9))
    fig_height = max(8, min(30, 3.6 + n_rows * 0.7))

    fig, ax = plt.subplots(figsize=(fig_width, fig_height))

    values = matrix.to_numpy(dtype=float)
    vmax = np.nanmax(values) if values.size else 0
    im = ax.imshow(values, aspect="auto", cmap="YlGnBu", vmin=0, vmax=vmax)

    ax.set_title(title, fontsize=20, fontweight="bold", pad=22)
    ax.set_xlabel("Success / failure mode", fontsize=15, fontweight="bold", labelpad=16)
    ax.set_ylabel("Scenario", fontsize=15, fontweight="bold", labelpad=14)

    ax.set_xticks(np.arange(n_cols))
    ax.set_yticks(np.arange(n_rows))

    ax.set_xticklabels(
        [display_wrapped_label(c, width=18) for c in matrix.columns],
        rotation=35,
        ha="right",
        fontsize=11,
    )
    ax.set_yticklabels([display_label(i, width=58) for i in matrix.index], fontsize=11)

    cbar = fig.colorbar(im, ax=ax)
    cbar_label = "Percentage" if normalise else "Count"
    cbar.set_label(cbar_label, fontsize=14, fontweight="bold", labelpad=14)
    cbar.ax.tick_params(labelsize=11)

    if annotate:
        midpoint = vmax / 2 if vmax else 0
        for row_idx in range(n_rows):
            for col_idx in range(n_cols):
                value = values[row_idx, col_idx]
                if normalise:
                    label = f"{value:.0f}%" if value >= 1 else ""
                else:
                    label = str(int(value)) if value > 0 else ""

                if label:
                    ax.text(
                        col_idx,
                        row_idx,
                        label,
                        ha="center",
                        va="center",
                        fontsize=10,
                        fontweight="bold",
                        color="white" if value > midpoint else "black",
                    )

    ax.set_xticks(np.arange(-0.5, n_cols, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, n_rows, 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=1.0, alpha=0.85)
    ax.tick_params(which="minor", bottom=False, left=False)
    ax.tick_params(axis="both", which="major", length=0)

    fig.tight_layout()
    fig.savefig(out_path, dpi=220, bbox_inches="tight")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dsn",
        default=(
            os.getenv("DATABASE_URL")
            or os.getenv("MORAL_EVALS_DATABASE_URL")
            or "postgresql+psycopg://postgres:postgres@localhost:5432/moral_evals"
        ),
        help="SQLAlchemy database URL. Defaults to DATABASE_URL, MORAL_EVALS_DATABASE_URL, or local moral_evals Postgres.",
    )
    parser.add_argument(
        "--table",
        default="rpt.case_run_trace_reporting",
        help="Reporting table/view to read from, e.g. rpt.case_run_trace_reporting.",
    )
    parser.add_argument(
        "--scenario-col",
        default=None,
        help="Column containing the scenario/case grouping key. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--scenario-label-col",
        default=None,
        help=(
            "Optional column containing display labels for scenarios. "
            "If omitted and case_pk exists, public.scenario.name is used when available."
        ),
    )
    parser.add_argument(
        "--mode-col",
        default=None,
        help="Column containing success/failure mode. Auto-detected if omitted.",
    )
    parser.add_argument(
        "--where",
        default=None,
        help=(
            "Optional SQL filter, without the WHERE keyword. "
            "Example: \"scorer_name = 'manual'\". "
            "Legacy dataset_alias and scorer_name='manual' filters are mapped onto current reporting columns when possible. "
            "Only use trusted local input."
        ),
    )
    parser.add_argument(
        "--top-n-scenarios",
        type=int,
        default=30,
        help="Limit to top N scenarios by total count. Use 0 for no limit.",
    )
    parser.add_argument(
        "--normalise",
        choices=["row", "column"],
        default=None,
        help="Optional normalisation. 'row' shows within-scenario percentages.",
    )
    parser.add_argument(
        "--out",
        default="tmp/failure_mode_heatmap.png",
        help="Output PNG path.",
    )
    parser.add_argument(
        "--counts-out",
        default="tmp/failure_mode_heatmap_counts.csv",
        help="Output CSV path for the pivoted matrix.",
    )
    parser.add_argument(
        "--title",
        default="Success/failure modes by scenario",
        help="Chart title.",
    )
    parser.add_argument(
        "--no-annotate",
        action="store_true",
        help="Disable numbers inside heatmap cells.",
    )
    parser.add_argument(
        "--list-columns",
        action="store_true",
        help="List available columns in --table and exit.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    engine = create_engine(sqlalchemy_dsn(args.dsn))
    columns = get_columns(engine, args.table)

    if not columns:
        raise RuntimeError(f"No columns found for table/view {args.table!r}. Does it exist?")

    if args.list_columns:
        print(f"Columns in {args.table}:")
        for col in columns:
            print(f"  - {col}")
        return

    scenario_col = choose_column(columns, args.scenario_col, SCENARIO_CANDIDATES, "scenario")
    scenario_label_col = None
    if args.scenario_label_col:
        scenario_label_col = choose_column(columns, args.scenario_label_col, [], "scenario label")
    mode_col = choose_column(columns, args.mode_col, MODE_CANDIDATES, "mode")

    print(f"Using table:        {args.table}")
    print(f"Using scenario col: {scenario_col}")
    print(
        "Using label col:    "
        + (scenario_label_col or ("public.scenario.name via case_pk" if "case_pk" in columns else scenario_col))
    )
    print(f"Using mode col:     {mode_col}")
    resolved_where = normalise_where_sql(args.where, columns)
    if args.where:
        print(f"Using filter:       {resolved_where}")

    counts = fetch_counts(
        engine,
        table=args.table,
        scenario_col=scenario_col,
        scenario_label_col=scenario_label_col,
        mode_col=mode_col,
        where_sql=args.where,
        columns=columns,
    )

    if counts.empty:
        print(
            "No rows found for the selected table/columns/filter. "
            "The selected slice may have no scored failure-class rows."
        )
        diagnostics = empty_selection_diagnostics(
            engine,
            table=args.table,
            mode_col=mode_col,
            columns=columns,
        )
        if not diagnostics.empty:
            print("\nAvailable rows by dataset_version:")
            print(diagnostics.to_string(index=False))
        raise SystemExit(2)

    top_n = None if args.top_n_scenarios == 0 else args.top_n_scenarios

    matrix = build_matrix(
        counts,
        top_n_scenarios=top_n,
        normalise=args.normalise,
    )

    counts_out = Path(args.counts_out)
    counts_out.parent.mkdir(parents=True, exist_ok=True)
    matrix.to_csv(counts_out)

    plot_heatmap(
        matrix,
        out_path=Path(args.out),
        title=args.title,
        normalise=args.normalise,
        annotate=not args.no_annotate,
    )

    print(f"Wrote heatmap: {args.out}")
    print(f"Wrote counts:  {args.counts_out}")


if __name__ == "__main__":
    main()
