"""The numeric knob reader of the test helpers."""

from __future__ import annotations

import pytest

from tests.helpers.env import EnvConfigError, positive_number


def test_unset_or_blank_gives_the_default() -> None:
    assert positive_number({}, "KNOB", 7, int) == 7
    assert positive_number({"KNOB": "  "}, "KNOB", 7.5, float) == 7.5


def test_a_set_value_is_cast_and_trimmed() -> None:
    assert positive_number({"KNOB": " 12 "}, "KNOB", 7, int) == 12
    assert positive_number({"KNOB": "0.25"}, "KNOB", 7.0, float) == 0.25


@pytest.mark.parametrize("value", ["abc", "1.5", "0", "-2", "nan"])
def test_a_bad_value_names_the_variable_and_the_value(value: str) -> None:
    with pytest.raises(EnvConfigError, match=f"KNOB must be a positive number, got '{value}'"):
        positive_number({"KNOB": value}, "KNOB", 7, int if value != "nan" else float)
