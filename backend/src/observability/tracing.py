from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()


class TracingContext:
    def __init__(self, langfuse: Any | None) -> None:
        self._langfuse = langfuse
        self._trace: Any | None = None

    def start_trace(
        self,
        *,
        name: str,
        user_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> Any | None:
        if self._langfuse is None:
            return None
        self._trace = self._langfuse.trace(
            name=name,
            user_id=user_id,
            metadata=metadata or {},
        )
        log.info("trace_started", name=name, user_id=user_id)
        return self._trace

    def start_generation(
        self,
        *,
        name: str,
        model: str,
        input_data: Any = None,
    ) -> Any | None:
        if self._trace is None:
            return None
        generation = self._trace.generation(
            name=name,
            model=model,
            input=input_data,
        )
        return generation

    def end_generation(self, generation: Any, output: Any) -> None:
        if generation is not None:
            generation.end(output=output)

    def start_span(self, *, name: str, input_data: Any = None) -> Any | None:
        if self._trace is None:
            return None
        return self._trace.span(name=name, input=input_data)

    def end_span(self, span: Any, output: Any) -> None:
        if span is not None:
            span.end(output=output)

    def update_trace(self, *, output: Any = None) -> None:
        if self._trace is not None:
            self._trace.update(output=output)

    def score(self, *, name: str, value: float, comment: str = "") -> None:
        if self._trace is not None:
            self._trace.score(name=name, value=value, comment=comment)

    def flush(self) -> None:
        if self._langfuse is not None:
            self._langfuse.flush()


def get_prompt_from_langfuse(
    langfuse: Any | None,
    *,
    name: str = "finadvisor-system",
    label: str = "production",
    fallback: str = "",
) -> str | None:
    if langfuse is None:
        return None
    try:
        prompt = langfuse.get_prompt(name, label=label)
        compiled: str = prompt.compile()
        log.info("langfuse_prompt_loaded", name=name, label=label)
        return compiled
    except Exception as exc:
        log.warning("langfuse_prompt_fallback", name=name, error=str(exc))
        return fallback if fallback else None
