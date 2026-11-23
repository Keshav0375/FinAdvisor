"""The `compose_only` collection rule: deselected, never skipped, without the compose stack."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from tests.integration.conftest import (
    COMPOSE_ONLY_MARKER,
    KAFKA_COMPOSE_ENV,
    pytest_collection_modifyitems,
    pytest_configure,
    split_compose_only,
)


class FakeItem:
    def __init__(self, name: str, compose_only: bool) -> None:
        self.name = name
        self._compose_only = compose_only

    def get_closest_marker(self, name: str) -> object | None:
        return object() if name == COMPOSE_ONLY_MARKER and self._compose_only else None


def _items() -> list[Any]:
    return [FakeItem("plain", False), FakeItem("needs_compose", True)]


def test_compose_only_items_are_deselected_without_compose() -> None:
    selected, deselected = split_compose_only(_items(), compose=False)
    assert [i.name for i in selected] == ["plain"]
    assert [i.name for i in deselected] == ["needs_compose"]


def test_compose_only_items_are_kept_with_compose() -> None:
    selected, deselected = split_compose_only(_items(), compose=True)
    assert [i.name for i in selected] == ["plain", "needs_compose"]
    assert deselected == []


@pytest.mark.parametrize(
    ("value", "kept"), [(None, ["plain"]), ("0", ["plain"]), ("1", ["plain", "needs_compose"])]
)
def test_hook_reports_deselected_items_and_trims_the_list(
    monkeypatch: pytest.MonkeyPatch, value: str | None, kept: list[str]
) -> None:
    if value is None:
        monkeypatch.delenv(KAFKA_COMPOSE_ENV, raising=False)
    else:
        monkeypatch.setenv(KAFKA_COMPOSE_ENV, value)
    config = SimpleNamespace(hook=mock.Mock())
    items = _items()
    pytest_collection_modifyitems(config, items)  # type: ignore[arg-type]
    assert [i.name for i in items] == kept
    if len(kept) == 1:
        [call] = config.hook.pytest_deselected.call_args_list
        assert [i.name for i in call.kwargs["items"]] == ["needs_compose"]
    else:
        config.hook.pytest_deselected.assert_not_called()


def test_configure_registers_the_marker() -> None:
    config = mock.Mock()
    pytest_configure(config)
    name, line = config.addinivalue_line.call_args.args
    assert name == "markers"
    assert line.startswith(f"{COMPOSE_ONLY_MARKER}:")
