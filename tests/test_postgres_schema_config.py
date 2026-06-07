from __future__ import annotations

import argparse
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from postgres_schema_config import (  # noqa: E402
    PostgresSchemas,
    add_schema_args,
    apply_search_path,
    op_relation,
    raw_relation,
    rpt_relation,
    schemas_from_args,
)


def parse_schema_args(*argv: str) -> PostgresSchemas:
    parser = argparse.ArgumentParser()
    add_schema_args(parser)
    return schemas_from_args(parser.parse_args(list(argv)))


class PostgresSchemaConfigTests(unittest.TestCase):
    SCHEMA_ENV = ("MORAL_EVALS_RAW_SCHEMA", "MORAL_EVALS_OP_SCHEMA", "MORAL_EVALS_RPT_SCHEMA")

    def test_default_schemas_are_public(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            for name in self.SCHEMA_ENV:
                os.environ.pop(name, None)

            schemas = parse_schema_args()

        self.assertEqual(schemas, PostgresSchemas(raw="public", op="public", rpt="public"))
        self.assertEqual(schemas.search_path, ("public",))

    def test_environment_variables_are_parser_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MORAL_EVALS_RAW_SCHEMA": "raw",
                "MORAL_EVALS_OP_SCHEMA": "public",
                "MORAL_EVALS_RPT_SCHEMA": "rpt",
            },
            clear=False,
        ):
            schemas = parse_schema_args()

        self.assertEqual(schemas, PostgresSchemas(raw="raw", op="public", rpt="rpt"))
        self.assertEqual(schemas.search_path, ("raw", "public", "rpt"))

    def test_cli_args_override_environment_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "MORAL_EVALS_RAW_SCHEMA": "env_raw",
                "MORAL_EVALS_OP_SCHEMA": "env_op",
                "MORAL_EVALS_RPT_SCHEMA": "env_rpt",
            },
            clear=False,
        ):
            schemas = parse_schema_args("--raw-schema", "raw", "--op-schema", "ops", "--rpt-schema", "rpt")

        self.assertEqual(schemas, PostgresSchemas(raw="raw", op="ops", rpt="rpt"))
        self.assertEqual(schemas.search_path, ("raw", "ops", "rpt", "public"))

    def test_blank_schema_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(SystemExit, "raw schema must not be blank"):
            parse_schema_args("--raw-schema", "   ")

    def test_relation_helpers_quote_schema_and_table_identifiers(self) -> None:
        schemas = PostgresSchemas(raw="raw; select x", op="ops schema", rpt='rpt"quoted')

        self.assertEqual(raw_relation(schemas, "source_file").as_string(None), '"raw; select x"."source_file"')
        self.assertEqual(op_relation(schemas, "score_event").as_string(None), '"ops schema"."score_event"')
        self.assertEqual(rpt_relation(schemas, "case_run_trace").as_string(None), '"rpt""quoted"."case_run_trace"')

    def test_apply_search_path_uses_quoted_unique_schema_order(self) -> None:
        class FakeConnection:
            query = None

            def execute(self, query):
                self.query = query

        fake = FakeConnection()

        apply_search_path(fake, PostgresSchemas(raw="raw", op="public", rpt="rpt"))

        self.assertEqual(fake.query.as_string(None), 'SET search_path TO "raw", "public", "rpt"')


if __name__ == "__main__":
    unittest.main()
