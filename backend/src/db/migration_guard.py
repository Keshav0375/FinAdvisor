"""Refuse to upgrade a database that schema.sql built but Alembic never marked.

Such a database already holds the baseline tables, so revision 0001 would fail half-way on
CREATE TABLE. It is never stamped automatically: an operator confirms the schema and stamps once.
"""

from __future__ import annotations

from collections.abc import Iterable

BASELINE_REVISION = "0001"
# Tables revision 0001 creates (the tables of backend/db/schema.sql).
BASELINE_TABLES = frozenset({"documents", "chunks", "suitability_rules"})
VERSION_TABLE = "alembic_version"
# One-off command, run from backend/ with the same database settings as `jobs migrate`.
STAMP_COMMAND = f"python -m alembic stamp {BASELINE_REVISION}"
# Key in alembic Config.attributes that turns the check on (only `jobs migrate` sets it, so
# `alembic stamp` and `alembic downgrade` stay usable on such a database).
REFUSE_UNVERSIONED_ATTR = "refuse_unversioned_schema"


class UnversionedSchemaError(RuntimeError):
    """The database has baseline tables but no alembic_version table."""


def check_versioned(table_names: Iterable[str]) -> None:
    """Raise UnversionedSchemaError when baseline tables exist without alembic_version."""
    names = set(table_names)
    if VERSION_TABLE in names:
        return
    found = sorted(BASELINE_TABLES & names)
    if found:
        raise UnversionedSchemaError(
            f"the database was built from schema.sql (tables {', '.join(found)} exist, "
            f"no {VERSION_TABLE} table); run `{STAMP_COMMAND}` once to mark it at revision "
            f"{BASELINE_REVISION}, then run migrate again"
        )
