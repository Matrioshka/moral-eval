from __future__ import annotations

import sys
from pathlib import Path

from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ingest_eval_artifacts_to_postgres import truncate_relations  # noqa: E402


class FakeCursor:
    def __init__(self, existing: set[str]) -> None:
        self.existing = existing
        self.executed: list[tuple[object, object]] = []
        self._next_row = None

    def execute(self, query, params=None) -> None:
        self.executed.append((query, params))
        if query == "select to_regclass(%s)":
            relation_name = params[0]
            self._next_row = (relation_name if relation_name in self.existing else None,)

    def fetchone(self):
        return self._next_row


def test_truncate_relations_skips_missing_relations() -> None:
    cur = FakeCursor(existing=set())

    truncate_relations(cur, [sql.SQL("public.missing_one"), sql.SQL("public.missing_two")])

    truncate_queries = [query for query, _params in cur.executed if query != "select to_regclass(%s)"]
    assert truncate_queries == []


def test_truncate_relations_still_truncates_existing_relations() -> None:
    cur = FakeCursor(existing={"public.present"})

    truncate_relations(cur, [sql.SQL("public.present"), sql.SQL("public.missing")])

    truncate_queries = [query for query, _params in cur.executed if query != "select to_regclass(%s)"]
    assert len(truncate_queries) == 1
    rendered = truncate_queries[0].as_string(None)
    assert "public.present" in rendered
    assert "public.missing" not in rendered
    assert "restart identity cascade" in rendered.lower()
