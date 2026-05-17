from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import UserClaims
from src.retrieval.embeddings import VoyageEmbeddings
from src.retrieval.vector_store import VectorStore

from .escalate import ESCALATE_TOOL, EscalateInput, escalate_to_compliance
from .lookup_factsheet import FACTSHEET_TOOL, FactsheetInput, lookup_product_factsheet
from .lookup_suitability import SUITABILITY_TOOL, SuitabilityInput, lookup_suitability_rule
from .search_kb import SEARCH_KB_TOOL, SearchKBInput, search_firm_kb

log = structlog.get_logger()


class ToolRegistry:
    def __init__(
        self,
        session: AsyncSession,
        embeddings: VoyageEmbeddings,
    ) -> None:
        self._session = session
        self._embeddings = embeddings
        self._vector_store = VectorStore(session)
        self._schemas: list[dict[str, Any]] = [
            SEARCH_KB_TOOL,
            SUITABILITY_TOOL,
            FACTSHEET_TOOL,
            ESCALATE_TOOL,
        ]
        self._input_models: dict[str, type[BaseModel]] = {
            "search_firm_kb": SearchKBInput,
            "lookup_suitability_rule": SuitabilityInput,
            "lookup_product_factsheet": FactsheetInput,
            "escalate_to_compliance": EscalateInput,
        }

    def to_anthropic_schema(self) -> list[dict[str, Any]]:
        return list(self._schemas)

    @property
    def tool_names(self) -> list[str]:
        return list(self._input_models.keys())

    async def execute(
        self,
        name: str,
        input_data: dict[str, Any],
        user: UserClaims,
    ) -> str:
        if name not in self._input_models:
            raise ValueError(f"Unknown tool: {name}")

        model_cls = self._input_models[name]
        parsed_input = model_cls.model_validate(input_data)

        log.info("tool_execute_start", tool=name, user=user.sub)

        result: BaseModel
        if name == "search_firm_kb":
            result = await search_firm_kb(
                parsed_input,  # type: ignore[arg-type]
                self._vector_store,
                self._embeddings,
            )
        elif name == "lookup_suitability_rule":
            result = await lookup_suitability_rule(
                parsed_input,  # type: ignore[arg-type]
                self._session,
                user,
            )
        elif name == "lookup_product_factsheet":
            result = await lookup_product_factsheet(
                parsed_input,  # type: ignore[arg-type]
                self._session,
                user,
            )
        elif name == "escalate_to_compliance":
            result = await escalate_to_compliance(
                parsed_input,  # type: ignore[arg-type]
                user,
            )
        else:
            raise ValueError(f"Unknown tool: {name}")

        output_json = result.model_dump_json()
        log.info("tool_execute_complete", tool=name, output_length=len(output_json))
        return output_json
