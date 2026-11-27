"""Read the test helpers' numeric knobs from the environment, at use time.

A bad value fails with a message that names the variable, not with a bare `ValueError`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping


class EnvConfigError(ValueError):
    """A helper knob in the environment is not a positive number (the message names it)."""


def positive_number[T: (int, float)](
    environ: Mapping[str, str], name: str, default: T, cast: Callable[[str], T]
) -> T:
    """`name` from `environ` as a positive number; `default` when unset or blank."""
    raw = environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise EnvConfigError(f"{name} must be a positive number, got {raw!r}") from None
    if not value > 0:  # also rejects nan
        raise EnvConfigError(f"{name} must be a positive number, got {raw!r}")
    return value
