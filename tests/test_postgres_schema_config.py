from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from postgres_schema_config import apply_search_path, op_relation, raw_relation, rpt_relation  # noqa: E402


class PostgresSchemaConfigTests(unittest.TestCase):
    SCHEMA_ENV = {
        "MORAL_EVALS_RAW_SCHEMA": "raw",
        "MORAL_EVALS_OP_SCHEMA": "public",
        "MORAL_EVALS_RPT_SCHEMA": "rpt",
    }

    def test_missing_schema_env_var_fails_clearly(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SystemExit, "MORAL_EVALS_RAW_SCHEMA must be set"):
                raw_relation("source_file")

    def test_relation_helpers_quote_schema_and_table_identifiers(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MORAL_EVALS_RAW_SCHEMA": "raw; select x",
                "MORAL_EVALS_OP_SCHEMA": "ops schema",
                "MORAL_EVALS_RPT_SCHEMA": 'rpt"quoted',
            },
            clear=True,
        ):
            self.assertEqual(raw_relation("source_file").as_string(None), '"raw; select x"."source_file"')
            self.assertEqual(op_relation("score_event").as_string(None), '"ops schema"."score_event"')
            self.assertEqual(rpt_relation("case_run_trace").as_string(None), '"rpt""quoted"."case_run_trace"')

    def test_apply_search_path_uses_active_runtime_order(self) -> None:
        class FakeConnection:
            query = None

            def execute(self, query):
                self.query = query

        fake = FakeConnection()

        with patch.dict(os.environ, self.SCHEMA_ENV, clear=True):
            apply_search_path(fake)

        self.assertEqual(fake.query.as_string(None), 'SET search_path TO "raw", "public", "rpt"')


if __name__ == "__main__":
    unittest.main()
