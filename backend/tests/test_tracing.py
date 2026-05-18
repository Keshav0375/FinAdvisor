from __future__ import annotations

from unittest.mock import MagicMock

from src.observability.tracing import TracingContext, get_prompt_from_langfuse


def test_tracing_context_noop_when_no_langfuse() -> None:
    ctx = TracingContext(None)
    assert ctx.start_trace(name="test", user_id="user1") is None
    assert ctx.start_generation(name="gen", model="claude") is None
    assert ctx.start_span(name="span") is None
    ctx.update_trace(output="text")
    ctx.score(name="accuracy", value=0.95)
    ctx.flush()


def test_tracing_context_creates_trace() -> None:
    mock_langfuse = MagicMock()
    mock_trace = MagicMock()
    mock_langfuse.trace.return_value = mock_trace

    ctx = TracingContext(mock_langfuse)
    result = ctx.start_trace(name="test_query", user_id="sarah_chen", metadata={"tier": "senior"})

    assert result is mock_trace
    mock_langfuse.trace.assert_called_once_with(
        name="test_query",
        user_id="sarah_chen",
        metadata={"tier": "senior"},
    )


def test_tracing_context_creates_generation() -> None:
    mock_langfuse = MagicMock()
    mock_trace = MagicMock()
    mock_generation = MagicMock()
    mock_langfuse.trace.return_value = mock_trace
    mock_trace.generation.return_value = mock_generation

    ctx = TracingContext(mock_langfuse)
    ctx.start_trace(name="q", user_id="u")
    gen = ctx.start_generation(name="iter_0", model="claude-sonnet-4-20250514")

    assert gen is mock_generation
    mock_trace.generation.assert_called_once()


def test_tracing_context_creates_span() -> None:
    mock_langfuse = MagicMock()
    mock_trace = MagicMock()
    mock_span = MagicMock()
    mock_langfuse.trace.return_value = mock_trace
    mock_trace.span.return_value = mock_span

    ctx = TracingContext(mock_langfuse)
    ctx.start_trace(name="q", user_id="u")
    span = ctx.start_span(name="tool_search_kb", input_data={"query": "bonds"})

    assert span is mock_span
    mock_trace.span.assert_called_once_with(name="tool_search_kb", input={"query": "bonds"})


def test_tracing_context_end_generation() -> None:
    mock_generation = MagicMock()
    ctx = TracingContext(None)
    ctx.end_generation(mock_generation, {"result": "ok"})
    mock_generation.end.assert_called_once_with(output={"result": "ok"})


def test_tracing_context_end_span() -> None:
    mock_span = MagicMock()
    ctx = TracingContext(None)
    ctx.end_span(mock_span, "tool output")
    mock_span.end.assert_called_once_with(output="tool output")


def test_tracing_context_score() -> None:
    mock_langfuse = MagicMock()
    mock_trace = MagicMock()
    mock_langfuse.trace.return_value = mock_trace

    ctx = TracingContext(mock_langfuse)
    ctx.start_trace(name="q", user_id="u")
    ctx.score(name="citation_accuracy", value=0.96, comment="all correct")

    mock_trace.score.assert_called_once_with(
        name="citation_accuracy", value=0.96, comment="all correct"
    )


def test_tracing_context_flush() -> None:
    mock_langfuse = MagicMock()
    ctx = TracingContext(mock_langfuse)
    ctx.flush()
    mock_langfuse.flush.assert_called_once()


def test_get_prompt_returns_none_when_no_langfuse() -> None:
    result = get_prompt_from_langfuse(None, name="test")
    assert result is None


def test_get_prompt_returns_compiled_prompt() -> None:
    mock_langfuse = MagicMock()
    mock_prompt = MagicMock()
    mock_prompt.compile.return_value = "You are a helpful advisor."
    mock_langfuse.get_prompt.return_value = mock_prompt

    result = get_prompt_from_langfuse(mock_langfuse, name="finadvisor-system", label="production")

    assert result == "You are a helpful advisor."
    mock_langfuse.get_prompt.assert_called_once_with("finadvisor-system", label="production")


def test_get_prompt_returns_fallback_on_error() -> None:
    mock_langfuse = MagicMock()
    mock_langfuse.get_prompt.side_effect = Exception("Not found")

    result = get_prompt_from_langfuse(mock_langfuse, name="missing", fallback="default prompt")
    assert result == "default prompt"


def test_get_prompt_returns_none_on_error_no_fallback() -> None:
    mock_langfuse = MagicMock()
    mock_langfuse.get_prompt.side_effect = Exception("Not found")

    result = get_prompt_from_langfuse(mock_langfuse, name="missing")
    assert result is None
