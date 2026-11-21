from __future__ import annotations

import pytest

from src.db.migration_guard import (
    BASELINE_TABLES,
    STAMP_COMMAND,
    UnversionedSchemaError,
    check_versioned,
)


def test_empty_database_passes() -> None:
    check_versioned([])


def test_versioned_database_with_baseline_tables_passes() -> None:
    check_versioned([*BASELINE_TABLES, "alembic_version"])


def test_unrelated_tables_without_version_table_pass() -> None:
    check_versioned(["some_other_table"])


def test_schema_sql_database_is_refused_with_the_stamp_command() -> None:
    with pytest.raises(UnversionedSchemaError) as raised:
        check_versioned(BASELINE_TABLES)
    message = str(raised.value)
    assert "schema.sql" in message
    assert f"`{STAMP_COMMAND}`" in message
    assert STAMP_COMMAND == "python -m alembic stamp 0001"
    assert "chunks, documents, suitability_rules" in message


def test_partial_baseline_without_version_table_is_refused() -> None:
    with pytest.raises(UnversionedSchemaError, match="tables documents exist"):
        check_versioned(["documents"])
