from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast

import anthropic
import structlog

from src.auth.models import UserClaims
from src.observability.tracing import TracingContext, get_prompt_from_langfuse

from .system_prompt import SYSTEM_PROMPT_VERSION, get_system_prompt
from .tools import ToolRegistry
from .types import StreamEvent, ToolCallEvent

log = structlog.get_logger()

DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 4096


class FinAdvisorAgent:
    def __init__(
        self,
        client: anthropic.AsyncAnthropic,
        tool_registry: ToolRegistry,
        langfuse: Any | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = client
        self._tools = tool_registry
        self._langfuse = langfuse
        self._model = model
        self._tracing = TracingContext(langfuse)

    async def run(
        self,
        query: str,
        user: UserClaims,
        *,
        max_iterations: int = 5,
    ) -> AsyncGenerator[StreamEvent, None]:
        self._tracing.start_trace(
            name="finadvisor_query",
            user_id=user.sub,
            metadata={
                "tier": user.tier,
                "jurisdictions": user.jurisdictions,
                "prompt_version": SYSTEM_PROMPT_VERSION,
            },
        )

        log.info(
            "agent_run_start",
            user=user.sub,
            query_length=len(query),
            max_iterations=max_iterations,
        )

        messages: list[dict[str, Any]] = [{"role": "user", "content": query}]

        versioned_prompt = get_prompt_from_langfuse(self._langfuse, name="finadvisor-system")
        system = versioned_prompt if versioned_prompt else get_system_prompt()

        for iteration in range(max_iterations):
            generation = self._tracing.start_generation(
                name=f"iteration_{iteration}",
                model=self._model,
                input_data=messages,
            )

            response = await self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=cast(Any, messages),
                tools=cast(Any, self._tools.to_anthropic_schema()),
            )

            self._tracing.end_generation(generation, response.model_dump())

            log.info(
                "agent_iteration",
                iteration=iteration,
                stop_reason=response.stop_reason,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            )

            if response.stop_reason == "tool_use":
                tool_results: list[dict[str, Any]] = []

                for block in response.content:
                    if block.type == "tool_use":
                        yield StreamEvent(
                            type="tool_call",
                            tool_call=ToolCallEvent(
                                tool_name=block.name,
                                tool_input=block.input,
                                tool_use_id=block.id,
                            ),
                        )

                        span = self._tracing.start_span(
                            name=f"tool_{block.name}",
                            input_data=block.input,
                        )

                        try:
                            result = await self._tools.execute(
                                name=block.name,
                                input_data=block.input,
                                user=user,
                            )
                        except Exception as exc:
                            log.error(
                                "tool_execution_error",
                                tool=block.name,
                                error=str(exc),
                            )
                            result = f'{{"error": "{exc!s}"}}'

                        self._tracing.end_span(span, result)

                        yield StreamEvent(type="tool_result", content=result)

                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                final_text = "".join(b.text for b in response.content if hasattr(b, "text"))

                self._tracing.update_trace(output=final_text)

                log.info(
                    "agent_run_complete",
                    iterations=iteration + 1,
                    response_length=len(final_text),
                )

                yield StreamEvent(type="text", content=final_text)
                return

        log.warning("agent_max_iterations", max_iterations=max_iterations)
        yield StreamEvent(
            type="error",
            content="Max reasoning iterations reached. Please simplify your query.",
        )
