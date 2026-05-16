# FinAdvisor — Architecture & Implementation Reference

> This document is the single source of truth for building FinAdvisor.
> Every service, schema, contract, and config is defined here.
> Claude Code: read this before writing any code.

---

## Table of Contents

1. [Project Structure](#1-project-structure)
2. [Service Map](#2-service-map)
3. [Database Schema](#3-database-schema)
4. [Ingest Pipeline](#4-ingest-pipeline)
5. [Auth & JWT](#5-auth--jwt)
6. [Agent Orchestrator](#6-agent-orchestrator)
7. [Tool Definitions](#7-tool-definitions)
8. [LLM Routing (LiteLLM + Kong)](#8-llm-routing-litellm--kong)
9. [PII Redaction](#9-pii-redaction)
10. [FastAPI Backend](#10-fastapi-backend)
11. [Next.js Frontend](#11-nextjs-frontend)
12. [LangFuse Integration](#12-langfuse-integration)
13. [Eval System](#13-eval-system)
14. [CI/CD Pipeline](#14-cicd-pipeline)
15. [Terraform IaC](#15-terraform-iac)
16. [Docker Compose (Local Dev)](#16-docker-compose-local-dev)
17. [Environment Variables](#17-environment-variables)
18. [Milestone Execution Order](#18-milestone-execution-order)

---

## 1. Project Structure

```
finadvisor/
├── README.md
├── mvp.md
├── architecture.md
├── docker-compose.yml                # Local dev: all services
├── docker-compose.override.yml       # Dev overrides (hot reload, debug)
├── .github/
│   └── workflows/
│       ├── ci.yml                    # Lint + test + typecheck
│       └── eval-gate.yml            # Eval gate on PR
│
├── terraform/
│   ├── main.tf
│   ├── variables.tf
│   ├── outputs.tf
│   ├── cloud_run.tf
│   ├── cloud_sql.tf
│   ├── cloud_storage.tf
│   ├── secrets.tf
│   ├── iam.tf
│   └── artifact_registry.tf
│
├── backend/                          # FastAPI + Agent (Python)
│   ├── Dockerfile
│   ├── pyproject.toml               # uv / pip managed
│   ├── src/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI app factory
│   │   ├── config.py                # Settings via pydantic-settings
│   │   ├── dependencies.py          # DI providers
│   │   │
│   │   ├── auth/
│   │   │   ├── __init__.py
│   │   │   ├── jwt.py              # JWT decode + verify
│   │   │   ├── models.py           # UserClaims pydantic model
│   │   │   └── mock_users.py       # Hardcoded user profiles for MVP
│   │   │
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py     # ReAct loop (Anthropic SDK)
│   │   │   ├── system_prompt.py    # Versioned system prompt
│   │   │   ├── tools/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── search_kb.py
│   │   │   │   ├── lookup_suitability.py
│   │   │   │   ├── lookup_factsheet.py
│   │   │   │   └── escalate.py
│   │   │   └── types.py            # Shared tool input/output models
│   │   │
│   │   ├── retrieval/
│   │   │   ├── __init__.py
│   │   │   ├── embeddings.py       # Voyage AI client
│   │   │   ├── vector_store.py     # pgvector query with RLS
│   │   │   └── models.py           # ChunkResult, RetrievalResponse
│   │   │
│   │   ├── pii/
│   │   │   ├── __init__.py
│   │   │   ├── redactor.py         # GCP DLP wrapper
│   │   │   └── fallback.py         # Regex fallback if DLP unavailable
│   │   │
│   │   ├── observability/
│   │   │   ├── __init__.py
│   │   │   ├── tracing.py          # LangFuse trace context
│   │   │   └── logging.py          # structlog config
│   │   │
│   │   ├── db/
│   │   │   ├── __init__.py
│   │   │   ├── session.py          # Async SQLAlchemy session factory
│   │   │   ├── models.py           # SQLAlchemy ORM models
│   │   │   └── rls.py              # SET session var helpers
│   │   │
│   │   └── api/
│   │       ├── __init__.py
│   │       ├── router.py           # Main API router
│   │       ├── chat.py             # POST /api/chat/stream (SSE)
│   │       ├── health.py           # GET /health
│   │       └── schemas.py          # Request/response pydantic models
│   │
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_rls.py             # Integration: RLS policy enforcement
│   │   ├── test_agent.py           # Unit: tool call routing
│   │   ├── test_pii.py             # Unit: redaction accuracy
│   │   └── test_auth.py            # Unit: JWT decode
│   │
│   └── scripts/
│       ├── ingest.py               # Ingest pipeline entry point
│       ├── generate_corpus.py      # Synthetic doc generator
│       ├── seed_mock_users.py      # Create mock JWT signing key + users
│       └── run_eval.py             # Eval runner (standalone)
│
├── frontend/                        # Next.js
│   ├── Dockerfile
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.js
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx            # Main chat page
│   │   │   └── globals.css
│   │   ├── components/
│   │   │   ├── ChatWindow.tsx
│   │   │   ├── MessageBubble.tsx
│   │   │   ├── CitationInline.tsx  # [1] markers
│   │   │   ├── CitationPanel.tsx   # Expanded citation view
│   │   │   ├── StaleBadge.tsx      # Orange warning badge
│   │   │   ├── UserSwitcher.tsx    # Profile dropdown
│   │   │   └── StreamingText.tsx   # SSE token renderer
│   │   ├── hooks/
│   │   │   ├── useChat.ts          # SSE connection + message state
│   │   │   └── useUser.ts          # Current user context
│   │   ├── lib/
│   │   │   ├── api.ts              # API client
│   │   │   ├── types.ts            # Shared types
│   │   │   └── users.ts            # Mock user profiles
│   │   └── stores/
│   │       └── chatStore.ts        # Zustand or useState
│   └── public/
│
├── kong/
│   ├── kong.yml                    # Declarative config
│   └── Dockerfile                  # Kong + plugins
│
├── litellm/
│   ├── config.yaml                 # Model routing + fallbacks
│   └── Dockerfile
│
├── langfuse/
│   └── docker-compose.langfuse.yml # LangFuse self-hosted
│
├── data/
│   ├── corpus/                     # Generated synthetic docs (JSON)
│   │   ├── product_factsheets/
│   │   ├── suitability_rules/
│   │   ├── compliance_memos/
│   │   └── jurisdiction_disclosures/
│   ├── eval/
│   │   ├── golden_qa.json          # 50 golden Q&A pairs
│   │   └── judge_prompt.txt        # LLM-as-judge system prompt
│   └── seed/
│       └── mock_users.json         # User profiles
│
└── docs/
    ├── demo_script.md              # Loom walkthrough script
    └── threat_model.md
```

---

## 2. Service Map

```
Port Allocation (local dev):

3000  → Next.js frontend
8000  → FastAPI backend
8001  → Kong AI Gateway
4000  → LiteLLM proxy
3030  → LangFuse web UI
5432  → PostgreSQL (app DB, pgvector)
5433  → PostgreSQL (LangFuse DB)
6379  → Redis (optional, rate limiting)
```

Request flow:

```
User (browser)
  → Next.js (:3000) — renders UI, streams SSE
    → FastAPI (:8000) — JWT verify, audit log, SSE endpoint
      → Agent Orchestrator — ReAct loop, tool calls
        → pgvector (RLS-filtered similarity search)
        → Kong (:8001) — token budget, PII guard
          → LiteLLM (:4000) — model routing + fallback
            → Claude / GPT-4o / Gemini
      → LangFuse (:3030) — trace emitted per turn
```

---

## 3. Database Schema

### App Database (PostgreSQL 16 + pgvector)

```sql
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================================
-- DOCUMENTS TABLE — source documents with metadata
-- ============================================================
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title           TEXT NOT NULL,
    doc_type        TEXT NOT NULL CHECK (doc_type IN (
                        'product_factsheet',
                        'suitability_rule',
                        'compliance_memo',
                        'jurisdiction_disclosure'
                    )),
    jurisdiction    TEXT NOT NULL CHECK (jurisdiction IN ('US', 'EU', 'UK', 'APAC')),
    tier_required   INT NOT NULL DEFAULT 1 CHECK (tier_required BETWEEN 1 AND 4),
                    -- 1=associate, 2=advisor, 3=senior, 4=private_wealth
    regulatory_ref  TEXT,           -- e.g. "FINRA Rule 2111"
    last_reviewed_at DATE NOT NULL,
    product_category TEXT,          -- e.g. "fixed_income", "equity", "alternatives"
    risk_level      TEXT CHECK (risk_level IN ('conservative', 'moderate', 'aggressive')),
    raw_content     TEXT NOT NULL,  -- original text (PII-redacted at ingest)
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ============================================================
-- CHUNKS TABLE — embedded chunks with RLS
-- ============================================================
CREATE TABLE chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,           -- PII-redacted chunk text
    embedding       vector(1024) NOT NULL,   -- Voyage AI voyage-3 = 1024 dims
    -- Denormalized from documents for RLS (avoids JOIN in hot path)
    jurisdiction    TEXT NOT NULL,
    tier_required   INT NOT NULL,
    regulatory_ref  TEXT,
    last_reviewed_at DATE NOT NULL,
    source_title    TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- Indexes
CREATE INDEX idx_chunks_embedding ON chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
    -- lists = 10 is fine for ~500 chunks; increase for larger corpus

CREATE INDEX idx_chunks_document_id ON chunks (document_id);
CREATE INDEX idx_chunks_jurisdiction ON chunks (jurisdiction);
CREATE INDEX idx_chunks_tier ON chunks (tier_required);

-- ============================================================
-- ROW-LEVEL SECURITY — the core differentiator
-- ============================================================
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;

-- Policy: user can only see chunks where:
--   1. chunk's tier_required <= user's tier level
--   2. chunk's jurisdiction is in user's jurisdiction list
CREATE POLICY chunk_visibility ON chunks
    FOR SELECT
    USING (
        tier_required <= current_setting('app.user_tier')::int
        AND jurisdiction = ANY(
            string_to_array(current_setting('app.user_jurisdictions'), ',')
        )
    );

-- Service role bypasses RLS (for ingest, admin)
CREATE ROLE finadvisor_service;
CREATE ROLE finadvisor_app;

GRANT SELECT ON chunks TO finadvisor_app;
GRANT ALL ON chunks TO finadvisor_service;
GRANT ALL ON documents TO finadvisor_service;
GRANT SELECT ON documents TO finadvisor_app;

-- finadvisor_app is subject to RLS
-- finadvisor_service bypasses (BYPASSRLS or table owner)

-- ============================================================
-- SUITABILITY RULES TABLE — structured rule lookup
-- ============================================================
CREATE TABLE suitability_rules (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_name           TEXT NOT NULL,
    product_category    TEXT NOT NULL,
    client_risk_profile TEXT NOT NULL CHECK (client_risk_profile IN (
                            'conservative', 'moderate', 'aggressive'
                        )),
    min_tier_required   INT NOT NULL DEFAULT 1,
    jurisdiction        TEXT NOT NULL,
    regulatory_ref      TEXT NOT NULL,
    rule_text           TEXT NOT NULL,
    last_reviewed_at    DATE NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT now()
);
```

### Setting RLS Session Variables (Critical Pattern)

Every request to the agent must set Postgres session vars before any query:

```python
# In db/rls.py
async def set_rls_context(
    session: AsyncSession,
    user: UserClaims,
) -> None:
    """Set Postgres session variables for RLS policy evaluation."""
    await session.execute(
        text("SET app.user_tier = :tier"),
        {"tier": str(user.tier_level)},
    )
    await session.execute(
        text("SET app.user_jurisdictions = :jurisdictions"),
        {"jurisdictions": ",".join(user.jurisdictions)},
    )
```

---

## 4. Ingest Pipeline

### Flow

```
data/corpus/*.json
  → scripts/generate_corpus.py     (creates synthetic docs)
  → scripts/ingest.py              (processes + loads)
      1. Read each JSON doc
      2. Send raw_content to GCP DLP API → get redacted_content
      3. Chunk redacted_content (RecursiveCharacterTextSplitter, 512 tokens, 50 overlap)
      4. Embed each chunk via Voyage AI (voyage-3, 1024 dims, batch API)
      5. INSERT into documents table (full doc metadata)
      6. INSERT into chunks table (chunk + embedding + denormalized metadata)
```

### Synthetic Document Format

```json
{
  "title": "Vanguard Total Bond Market ETF — Product Fact Sheet",
  "doc_type": "product_factsheet",
  "jurisdiction": "US",
  "tier_required": 1,
  "regulatory_ref": "FINRA Rule 2111, SEC Rule 17a-4",
  "last_reviewed_at": "2025-03-15",
  "product_category": "fixed_income",
  "risk_level": "conservative",
  "content": "The Vanguard Total Bond Market ETF (BND) provides broad exposure to U.S. investment-grade bonds..."
}
```

### Corpus Generation Strategy

Use Claude to generate 50 synthetic documents via `scripts/generate_corpus.py`:

| Category | Count | Tier Range | Jurisdictions |
|----------|-------|-----------|---------------|
| Product fact sheets | 20 | 1-4 | US, EU, UK |
| Suitability rules | 10 | 1-3 | US, EU, UK |
| Compliance memos | 10 | 2-4 | US, EU |
| Jurisdiction disclosures | 10 | 1-2 | US, EU, UK, APAC |

Requirements for generation:
- Realistic financial language (not lorem ipsum)
- Each doc 300-800 words
- Product names should be recognizable but fictional firm context ("Meridian Wealth Partners")
- Regulatory references must be real rule numbers (FINRA 2111, MiFID II Article 25, etc.)
- Include some docs with `last_reviewed_at` > 12 months ago (to trigger stale warnings)
- Include some docs with embedded PII patterns (names, account numbers) to test DLP redaction

---

## 5. Auth & JWT

### Mock User Profiles

```python
# auth/mock_users.py
from typing import Literal

MOCK_USERS: dict[str, dict] = {
    "sarah_chen": {
        "sub": "sarah_chen",
        "name": "Sarah Chen",
        "tier": "senior",          # tier_level = 3
        "tier_level": 3,
        "jurisdictions": ["US"],
        "licenses": ["Series-7", "Series-66"],
    },
    "alex_kim": {
        "sub": "alex_kim",
        "name": "Alex Kim",
        "tier": "associate",       # tier_level = 1
        "tier_level": 1,
        "jurisdictions": ["EU"],
        "licenses": ["MiFID-II"],
    },
    "james_wright": {
        "sub": "james_wright",
        "name": "James Wright",
        "tier": "private_wealth",  # tier_level = 4
        "tier_level": 4,
        "jurisdictions": ["UK"],
        "licenses": ["FCA"],
    },
    "priya_sharma": {
        "sub": "priya_sharma",
        "name": "Priya Sharma",
        "tier": "advisor",         # tier_level = 2
        "tier_level": 2,
        "jurisdictions": ["US", "EU"],
        "licenses": ["Series-7", "MiFID-II"],
    },
}
```

### JWT Structure

```python
# auth/models.py
from pydantic import BaseModel

class UserClaims(BaseModel):
    sub: str                          # user ID
    name: str
    tier: str                         # associate / advisor / senior / private_wealth
    tier_level: int                   # 1-4
    jurisdictions: list[str]          # ["US", "EU"]
    licenses: list[str]              # ["Series-7", "MiFID-II"]
```

### JWT Flow (MVP)

1. Frontend sends `X-User-Id: sarah_chen` header (MVP shortcut — no real IdP)
2. Backend middleware looks up user in `MOCK_USERS`, builds `UserClaims`
3. For production readiness: swap to real JWT verification with `python-jose` — the `UserClaims` model stays identical

```python
# auth/jwt.py
from fastapi import Request, HTTPException
from .mock_users import MOCK_USERS
from .models import UserClaims

async def get_current_user(request: Request) -> UserClaims:
    user_id = request.headers.get("X-User-Id")
    if not user_id or user_id not in MOCK_USERS:
        raise HTTPException(status_code=401, detail="Unknown user")
    return UserClaims(**MOCK_USERS[user_id])
```

---

## 6. Agent Orchestrator

### ReAct Loop (Anthropic SDK)

```python
# agent/orchestrator.py
import anthropic
from langfuse import Langfuse

class FinAdvisorAgent:
    def __init__(
        self,
        client: anthropic.Anthropic,        # Injected — points to LiteLLM
        langfuse: Langfuse,                  # Injected
        tool_registry: ToolRegistry,         # Injected
    ):
        self.client = client
        self.langfuse = langfuse
        self.tools = tool_registry

    async def run(
        self,
        query: str,
        user: UserClaims,
        *,
        max_iterations: int = 5,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        ReAct loop:
        1. Send user query + system prompt + tools to Claude
        2. If response has tool_use blocks → execute tools → append results
        3. Loop until text response (no more tool calls) or max_iterations
        4. Stream final text response as SSE events
        """
        trace = self.langfuse.trace(
            name="finadvisor_query",
            user_id=user.sub,
            metadata={"tier": user.tier, "jurisdictions": user.jurisdictions},
        )

        messages = [{"role": "user", "content": query}]
        system = get_system_prompt()  # Versioned via LangFuse

        for iteration in range(max_iterations):
            generation = trace.generation(
                name=f"iteration_{iteration}",
                model="claude-sonnet-4-20250514",
                input=messages,
            )

            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                system=system,
                messages=messages,
                tools=self.tools.to_anthropic_schema(),
            )

            generation.end(output=response.model_dump())

            # Check if response contains tool calls
            if response.stop_reason == "tool_use":
                # Execute each tool call
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        span = trace.span(name=f"tool_{block.name}")
                        result = await self.tools.execute(
                            name=block.name,
                            input=block.input,
                            user=user,
                        )
                        span.end(output=result)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                # Append assistant response + tool results to messages
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                # Final text response — stream it
                final_text = "".join(
                    b.text for b in response.content if b.type == "text"
                )
                trace.update(output=final_text)
                yield StreamEvent(type="text", content=final_text)
                return

        # Max iterations reached
        yield StreamEvent(
            type="error",
            content="Max reasoning iterations reached. Please simplify your query.",
        )
```

### System Prompt

```python
# agent/system_prompt.py

SYSTEM_PROMPT = """You are FinAdvisor, a compliance-aware wealth advisory assistant 
for Meridian Wealth Partners. You help financial advisors find product information, 
suitability rules, and compliance guidance.

CRITICAL RULES:
1. EVERY factual claim MUST cite a retrieved source using [N] notation.
2. Each citation MUST include: source document title, regulatory reference, 
   and last-reviewed date.
3. If no retrieved source supports a claim, say "I don't have firm documentation 
   on this topic" — DO NOT make unsourced claims.
4. If a product is outside the advisor's licensed scope, use the escalate_to_compliance 
   tool and inform the advisor.
5. NEVER fabricate regulatory references. Only cite refs that appear in retrieved chunks.
6. If a cited document's last_reviewed_at is more than 12 months ago, note:
   "⚠️ This source was last reviewed on [date] — please verify with Compliance."

RESPONSE FORMAT:
- Answer the advisor's question using retrieved firm documentation
- Inline citations: [1], [2], etc.
- At the end, list sources:
  [1] {doc_title} — {regulatory_ref} (reviewed: {last_reviewed_at})
  [2] ...

AVAILABLE TOOLS:
- search_firm_kb: Search the firm's knowledge base for relevant documents
- lookup_suitability_rule: Look up suitability rules by product category and client profile
- lookup_product_factsheet: Get full product fact sheet by name or ticker
- escalate_to_compliance: Flag query for compliance review (restricted products, licensing issues)

Always start by searching the knowledge base. Use multiple tools if needed to build 
a complete, well-cited answer."""
```

---

## 7. Tool Definitions

### search_firm_kb

```python
# agent/tools/search_kb.py
from pydantic import BaseModel, Field

class SearchKBInput(BaseModel):
    query: str = Field(description="Natural language search query")
    top_k: int = Field(default=5, le=10, description="Number of results")

class ChunkResult(BaseModel):
    chunk_id: str
    content: str
    source_title: str
    regulatory_ref: str | None
    last_reviewed_at: str
    similarity_score: float

class SearchKBOutput(BaseModel):
    results: list[ChunkResult]
    total_found: int

# Anthropic tool schema
SEARCH_KB_TOOL = {
    "name": "search_firm_kb",
    "description": (
        "Search the firm's knowledge base for relevant product information, "
        "compliance rules, and policy documents. Results are filtered by the "
        "advisor's tier and jurisdiction automatically."
    ),
    "input_schema": SearchKBInput.model_json_schema(),
}
```

### lookup_suitability_rule

```python
class SuitabilityInput(BaseModel):
    product_category: str = Field(
        description="Product category: fixed_income, equity, alternatives, structured"
    )
    client_risk_profile: str = Field(
        description="Client risk profile: conservative, moderate, aggressive"
    )

SUITABILITY_TOOL = {
    "name": "lookup_suitability_rule",
    "description": (
        "Look up the firm's suitability rules for a specific product category "
        "and client risk profile. Returns applicable rules with regulatory refs."
    ),
    "input_schema": SuitabilityInput.model_json_schema(),
}
```

### lookup_product_factsheet

```python
class FactsheetInput(BaseModel):
    product_name: str = Field(
        description="Product name or ticker to look up"
    )

FACTSHEET_TOOL = {
    "name": "lookup_product_factsheet",
    "description": (
        "Retrieve the full product fact sheet for a specific investment product. "
        "Returns product details, risk classification, and regulatory information."
    ),
    "input_schema": FactsheetInput.model_json_schema(),
}
```

### escalate_to_compliance

```python
class EscalateInput(BaseModel):
    reason: str = Field(
        description="Why this query needs compliance review"
    )
    product_class: str = Field(
        description="The restricted product class involved"
    )
    advisor_licenses: list[str] = Field(
        description="Advisor's current licenses"
    )

ESCALATE_TOOL = {
    "name": "escalate_to_compliance",
    "description": (
        "Flag a query for compliance department review. Use when: "
        "(1) query involves a product class outside advisor's licenses, "
        "(2) regulatory conflict detected, "
        "(3) suitability determination is ambiguous and requires human review."
    ),
    "input_schema": EscalateInput.model_json_schema(),
}
```

### Tool Registry Pattern

```python
# agent/tools/__init__.py
class ToolRegistry:
    """DI-friendly tool registry. Each tool receives its dependencies at init."""

    def __init__(
        self,
        vector_store: VectorStore,
        db_session: AsyncSession,
    ):
        self._tools = {
            "search_firm_kb": SearchKBTool(vector_store=vector_store),
            "lookup_suitability_rule": SuitabilityTool(db_session=db_session),
            "lookup_product_factsheet": FactsheetTool(vector_store=vector_store),
            "escalate_to_compliance": EscalateTool(),
        }

    def to_anthropic_schema(self) -> list[dict]:
        return [tool.schema for tool in self._tools.values()]

    async def execute(
        self, name: str, input: dict, user: UserClaims
    ) -> str:
        tool = self._tools[name]
        result = await tool.run(input, user)
        return result.model_dump_json()
```

---

## 8. LLM Routing (LiteLLM + Kong)

### LiteLLM Config

```yaml
# litellm/config.yaml
model_list:
  - model_name: "claude-sonnet-4-20250514"
    litellm_params:
      model: "claude-sonnet-4-20250514"
      api_key: "os.environ/ANTHROPIC_API_KEY"
    model_info:
      id: "claude-primary"

  - model_name: "claude-sonnet-4-20250514"
    litellm_params:
      model: "openai/gpt-4o"
      api_key: "os.environ/OPENAI_API_KEY"
    model_info:
      id: "gpt4o-fallback"

  - model_name: "claude-sonnet-4-20250514"
    litellm_params:
      model: "gemini/gemini-2.5-flash"
      api_key: "os.environ/GOOGLE_API_KEY"
    model_info:
      id: "gemini-fallback"

router_settings:
  routing_strategy: "simple-shuffle"    # Primary first, then fallbacks
  num_retries: 2
  retry_after: 5
  fallbacks:
    - claude-primary: [gpt4o-fallback, gemini-fallback]
  allowed_fails: 3                       # Circuit breaker: 3 fails → skip for cooldown
  cooldown_time: 300                     # 5 min cooldown

litellm_settings:
  drop_params: true
  set_verbose: false

general_settings:
  master_key: "os.environ/LITELLM_MASTER_KEY"
```

### Kong AI Gateway Config

```yaml
# kong/kong.yml
_format_version: "3.0"

services:
  - name: llm-service
    url: http://litellm:4000
    routes:
      - name: llm-route
        paths:
          - /v1
        strip_path: false

plugins:
  # Token budget per user
  - name: rate-limiting
    config:
      minute: 60
      hour: 500
      policy: local
      identifier: header
      header_name: X-User-Id

  # Request size limit (prevents massive prompts)
  - name: request-size-limiting
    config:
      allowed_payload_size: 1       # 1 MB

  # Request/response logging
  - name: http-log
    config:
      http_endpoint: http://backend:8000/api/audit/kong
      method: POST
      content_type: application/json
```

### How the Backend Connects

```python
# The backend's Anthropic client points to Kong, not directly to Anthropic
client = anthropic.Anthropic(
    api_key=settings.litellm_master_key,     # LiteLLM master key
    base_url=f"{settings.kong_url}/v1",       # Kong → LiteLLM → Claude
)
```

---

## 9. PII Redaction

### Two-Pass Architecture

```
INGEST PASS:
  Raw doc text → GCP DLP inspect → Replace PII with tokens → Store redacted text
  "John Smith's account #482177 shows..."
  → "[CLIENT_NAME_1]'s account [ACCT_1] shows..."

OUTPUT PASS:
  LLM response text → GCP DLP inspect → Replace any PII that leaked → Stream to user
```

### Implementation

```python
# pii/redactor.py
from google.cloud import dlp_v2

class PIIRedactor:
    INFO_TYPES = [
        "PERSON_NAME", "PHONE_NUMBER", "EMAIL_ADDRESS",
        "US_SOCIAL_SECURITY_NUMBER", "CANADA_SOCIAL_INSURANCE_NUMBER",
        "FINANCIAL_ACCOUNT_NUMBER", "CREDIT_CARD_NUMBER",
        "STREET_ADDRESS",
    ]

    def __init__(self, project_id: str):
        self.client = dlp_v2.DlpServiceClient()
        self.project_id = project_id

    def redact(self, text: str) -> tuple[str, list[dict]]:
        """
        Returns (redacted_text, findings).
        findings = [{"type": "PERSON_NAME", "original": "John Smith", "replacement": "[CLIENT_NAME_1]"}]
        """
        inspect_config = {
            "info_types": [{"name": t} for t in self.INFO_TYPES],
            "min_likelihood": "LIKELY",
        }
        deidentify_config = {
            "info_type_transformations": {
                "transformations": [{
                    "primitive_transformation": {
                        "replace_with_info_type_config": {}
                    }
                }]
            }
        }

        parent = f"projects/{self.project_id}/locations/global"
        response = self.client.deidentify_content(
            request={
                "parent": parent,
                "inspect_config": inspect_config,
                "deidentify_config": deidentify_config,
                "item": {"value": text},
            }
        )
        return response.item.value, self._extract_findings(response)
```

### Regex Fallback (For Local Dev Without GCP)

```python
# pii/fallback.py
import re

PATTERNS = {
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "PHONE": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    "EMAIL": r"\b[\w.-]+@[\w.-]+\.\w+\b",
    "ACCOUNT": r"\b\d{4}[-]?\d{4}\b",
}

def regex_redact(text: str) -> str:
    for label, pattern in PATTERNS.items():
        text = re.sub(pattern, f"[{label}_REDACTED]", text)
    return text
```

---

## 10. FastAPI Backend

### App Factory

```python
# main.py
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB pool, LangFuse, embeddings client
    app.state.db = await create_db_pool(settings.database_url)
    app.state.langfuse = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    app.state.embeddings = VoyageEmbeddings(api_key=settings.voyage_api_key)
    yield
    # Shutdown
    await app.state.db.dispose()
    app.state.langfuse.shutdown()

def create_app() -> FastAPI:
    app = FastAPI(title="FinAdvisor API", lifespan=lifespan)
    app.include_router(api_router, prefix="/api")
    return app
```

### SSE Streaming Endpoint

```python
# api/chat.py
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: UserClaims = Depends(get_current_user),
    agent: FinAdvisorAgent = Depends(get_agent),
    db: AsyncSession = Depends(get_db_session),
    redactor: PIIRedactor = Depends(get_redactor),
):
    # Set RLS context BEFORE agent runs
    await set_rls_context(db, user)

    async def event_generator():
        async for event in agent.run(request.message, user):
            if event.type == "text":
                # Output PII pass
                redacted = redactor.redact(event.content)
                yield {
                    "event": "message",
                    "data": json.dumps({
                        "type": "text",
                        "content": redacted,
                    }),
                }
            elif event.type == "tool_call":
                yield {
                    "event": "tool",
                    "data": json.dumps({
                        "tool": event.tool_name,
                        "status": event.status,
                    }),
                }
            elif event.type == "citation":
                yield {
                    "event": "citation",
                    "data": json.dumps(event.citation.model_dump()),
                }
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())
```

### Request/Response Models

```python
# api/schemas.py
from pydantic import BaseModel
from datetime import date

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None

class Citation(BaseModel):
    index: int                    # [1], [2], etc.
    source_title: str
    regulatory_ref: str | None
    last_reviewed_at: date
    chunk_preview: str            # First 200 chars of chunk
    is_stale: bool                # True if last_reviewed_at > 12 months ago

class ChatResponse(BaseModel):
    content: str
    citations: list[Citation]
    compliance_flags: list[str]   # Any escalation reasons
    trace_id: str                 # LangFuse trace ID
```

---

## 11. Next.js Frontend

### Key Components

**ChatWindow.tsx** — Main chat container. Manages message history, sends requests, handles SSE stream.

**MessageBubble.tsx** — Renders a single message. Parses `[1]`, `[2]` markers in text and replaces with `<CitationInline>` components.

**CitationInline.tsx** — Clickable `[1]` badge. On hover/click, expands to show `<CitationPanel>`.

**CitationPanel.tsx** — Expanded citation view:
```
📄 Vanguard Total Bond Market ETF — Product Fact Sheet
📑 FINRA Rule 2111 — Suitability
📅 Reviewed: 2025-03-15
───
"The Vanguard Total Bond Market ETF (BND) provides broad exposure
to U.S. investment-grade bonds..."
```

**StaleBadge.tsx** — Orange `⚠️ STALE` badge if `last_reviewed_at` > 12 months.

**UserSwitcher.tsx** — Dropdown at top of page. Switches between mock users. Sends `X-User-Id` header with each request.

### SSE Hook

```typescript
// hooks/useChat.ts
export function useChat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const { currentUser } = useUser();

  const sendMessage = async (content: string) => {
    setIsStreaming(true);
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-User-Id": currentUser.sub,
      },
      body: JSON.stringify({ message: content }),
    });

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();

    // Parse SSE events, accumulate text, extract citations
    // Update message state progressively
  };

  return { messages, sendMessage, isStreaming };
}
```

---

## 12. LangFuse Integration

### Trace Structure

```
Trace: finadvisor_query
├── user_id: "sarah_chen"
├── metadata: { tier: "senior", jurisdictions: ["US"] }
│
├── Generation: iteration_0 (Claude call #1)
│   ├── input: [user message + system prompt]
│   ├── output: [tool_use: search_firm_kb]
│   └── model: claude-sonnet-4-20250514
│
├── Span: tool_search_firm_kb
│   ├── input: { query: "bond ETF suitability conservative" }
│   └── output: [5 chunks with scores]
│
├── Generation: iteration_1 (Claude call #2)
│   ├── input: [user msg + tool result]
│   ├── output: [final text with citations]
│   └── model: claude-sonnet-4-20250514
│
└── Score: citation_accuracy = 0.96
```

### Prompt Versioning

```python
# Fetch system prompt from LangFuse (enables version tracking + rollback)
def get_system_prompt() -> str:
    prompt = langfuse.get_prompt("finadvisor-system", label="production")
    return prompt.compile()
```

---

## 13. Eval System

### Golden Q&A Format

```json
// data/eval/golden_qa.json
{
  "eval_set": [
    {
      "id": "eval_001",
      "category": "product_suitability",
      "user_profile": "sarah_chen",
      "question": "Is the Vanguard Total Bond Market ETF suitable for a conservative retiree?",
      "expected_behavior": "Should retrieve US fixed-income product fact sheets and FINRA suitability rules. Answer should cite specific chunks with reg refs.",
      "must_cite_refs": ["FINRA Rule 2111"],
      "must_not_contain": ["MiFID", "FCA"],
      "expected_tool_calls": ["search_firm_kb", "lookup_suitability_rule"]
    },
    {
      "id": "eval_002",
      "category": "jurisdiction_scoping",
      "user_profile": "alex_kim",
      "question": "What US Treasury products can I recommend?",
      "expected_behavior": "Should NOT return US-only products since Alex is EU-jurisdiction. Should explain jurisdiction limitation.",
      "must_cite_refs": [],
      "must_not_contain": ["Series-7"],
      "expected_tool_calls": ["search_firm_kb"]
    },
    {
      "id": "eval_003",
      "category": "refusal",
      "user_profile": "alex_kim",
      "question": "Show me the private wealth tier structured products",
      "expected_behavior": "Should refuse or escalate — associate tier cannot see private_wealth (tier 4) products. RLS should return empty results.",
      "must_cite_refs": [],
      "expected_tool_calls": ["search_firm_kb", "escalate_to_compliance"]
    }
  ]
}
```

### LLM-as-Judge Prompt

```text
// data/eval/judge_prompt.txt
You are an evaluator for a financial advisory RAG system. You will be given:
- The advisor's question
- The system's response
- The retrieved source chunks

Score on these dimensions (0.0 to 1.0):

1. FAITHFULNESS: Does every claim in the response have supporting evidence 
   in the retrieved chunks? Score 0.0 if any claim is unsupported.

2. CITATION_ACCURACY: Are citations correctly mapped? Does [1] actually 
   reference the chunk it claims to? Is the regulatory ref real and present 
   in the source? Score 0.0 if any citation is fabricated.

3. REFUSAL_CORRECTNESS: If the query should have been refused or escalated 
   (out-of-scope, wrong jurisdiction), did the system correctly refuse? 
   Score 1.0 if correctly handled, 0.0 if it answered when it shouldn't have.

Respond in JSON only:
{
  "faithfulness": 0.0-1.0,
  "citation_accuracy": 0.0-1.0,
  "refusal_correctness": 0.0-1.0,
  "reasoning": "brief explanation"
}
```

### Eval Runner

```python
# scripts/run_eval.py
"""
Standalone eval runner. Used in CI/CD and manual testing.

Usage:
  python scripts/run_eval.py --eval-set data/eval/golden_qa.json --output results.json
  python scripts/run_eval.py --compare-baseline  # Compare vs LangFuse prod scores
"""
```

---

## 14. CI/CD Pipeline

### Eval Gate Workflow

```yaml
# .github/workflows/eval-gate.yml
name: Eval Gate

on:
  pull_request:
    branches: [main]

jobs:
  lint-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install deps
        run: pip install -e ".[dev]"
      - name: Lint
        run: ruff check .
      - name: Typecheck
        run: mypy src/
      - name: Unit tests
        run: pytest tests/ -x

  eval-gate:
    needs: lint-and-test
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env:
          POSTGRES_DB: finadvisor_test
          POSTGRES_USER: test
          POSTGRES_PASSWORD: test
        ports:
          - 5432:5432

    steps:
      - uses: actions/checkout@v4

      - name: Setup DB + seed eval data
        run: |
          psql -h localhost -U test -d finadvisor_test -f db/schema.sql
          python scripts/ingest.py --target test

      - name: Run eval set
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          LANGFUSE_PUBLIC_KEY: ${{ secrets.LANGFUSE_PUBLIC_KEY }}
          LANGFUSE_SECRET_KEY: ${{ secrets.LANGFUSE_SECRET_KEY }}
        run: |
          python scripts/run_eval.py \
            --eval-set data/eval/golden_qa.json \
            --output eval_results.json

      - name: Check thresholds
        run: |
          python -c "
          import json, sys
          results = json.load(open('eval_results.json'))
          faith = results['avg_faithfulness']
          cite = results['avg_citation_accuracy']
          print(f'Faithfulness: {faith:.2%}')
          print(f'Citation accuracy: {cite:.2%}')
          if cite < 0.95:
              print('❌ CITATION ACCURACY BELOW 95% — DEPLOY BLOCKED')
              sys.exit(1)
          if faith < 0.90:
              print('❌ FAITHFULNESS BELOW 90% — DEPLOY BLOCKED')
              sys.exit(1)
          print('✅ Eval gate passed')
          "

      - name: Upload results as artifact
        uses: actions/upload-artifact@v4
        with:
          name: eval-results
          path: eval_results.json
```

---

## 15. Terraform IaC

### Key Resources

```hcl
# terraform/cloud_sql.tf
resource "google_sql_database_instance" "finadvisor" {
  name             = "finadvisor-db"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier = "db-f1-micro"    # Smallest for MVP
    database_flags {
      name  = "cloudsql.enable_pgvector"
      value = "on"
    }
  }
}

# terraform/cloud_run.tf
resource "google_cloud_run_v2_service" "backend" {
  name     = "finadvisor-backend"
  location = var.region

  template {
    containers {
      image = "${var.artifact_registry}/backend:latest"
      env { name = "DATABASE_URL"; value_source { secret_key_ref { secret = "db-url" } } }
      env { name = "KONG_URL"; value = google_cloud_run_v2_service.kong.uri }
      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }
    }
  }
}
```

---

## 16. Docker Compose (Local Dev)

```yaml
# docker-compose.yml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: finadvisor
      POSTGRES_USER: finadvisor
      POSTGRES_PASSWORD: localdev
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./backend/db/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql

  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      DATABASE_URL: postgresql+asyncpg://finadvisor:localdev@postgres:5432/finadvisor
      KONG_URL: http://kong:8000
      LANGFUSE_HOST: http://langfuse:3000
      VOYAGE_API_KEY: ${VOYAGE_API_KEY}
      PII_MODE: regex         # Use regex fallback locally (no GCP DLP)
    depends_on:
      - postgres
      - kong

  litellm:
    build: ./litellm
    ports:
      - "4000:4000"
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      GOOGLE_API_KEY: ${GOOGLE_API_KEY}
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY}

  kong:
    build: ./kong
    ports:
      - "8001:8000"
    environment:
      KONG_DATABASE: "off"
      KONG_DECLARATIVE_CONFIG: /etc/kong/kong.yml
    depends_on:
      - litellm

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      NEXT_PUBLIC_API_URL: http://localhost:8000

  langfuse:
    image: langfuse/langfuse:2
    ports:
      - "3030:3000"
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      NEXTAUTH_SECRET: mysecret
      NEXTAUTH_URL: http://localhost:3030
    depends_on:
      - langfuse-db

  langfuse-db:
    image: postgres:16
    environment:
      POSTGRES_DB: langfuse
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse
    ports:
      - "5433:5432"

volumes:
  pgdata:
```

---

## 17. Environment Variables

```bash
# .env (local dev — never commit)

# LLM Providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
LITELLM_MASTER_KEY=sk-litellm-local

# Embeddings
VOYAGE_API_KEY=voyage-...

# Database
DATABASE_URL=postgresql+asyncpg://finadvisor:localdev@localhost:5432/finadvisor

# PII
PII_MODE=regex                    # "gcp_dlp" in production
GCP_PROJECT_ID=finadvisor-prod    # Only needed for PII_MODE=gcp_dlp

# Kong
KONG_URL=http://localhost:8001

# LangFuse
LANGFUSE_HOST=http://localhost:3030
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...

# Auth
JWT_SECRET=local-dev-secret       # For signing mock JWTs
```

---

## 18. Milestone Execution Order

Build in this exact sequence. Each milestone is independently testable.

### M1 — Project Scaffold + DB Schema (2 hrs)
**Goal:** Repo structure, Docker Compose up, Postgres with pgvector + RLS policies created.
**Test:** `docker compose up`, connect to Postgres, verify extensions and RLS policies exist.
**Files:** `docker-compose.yml`, `backend/Dockerfile`, `backend/db/schema.sql`, `backend/pyproject.toml`, `backend/src/config.py`

### M2 — Synthetic Corpus + Ingest Pipeline (3 hrs)
**Goal:** 50 synthetic docs generated, chunked, embedded, loaded into pgvector.
**Test:** Query `SELECT count(*) FROM chunks;` returns ~200-300 rows. Run a raw vector search to verify results.
**Files:** `scripts/generate_corpus.py`, `scripts/ingest.py`, `data/corpus/`, `backend/src/retrieval/embeddings.py`, `backend/src/pii/fallback.py`

### M3 — RLS Verification + Auth Layer (2 hrs)
**Goal:** Mock JWT auth working. RLS filters chunks correctly per user profile.
**Test:** Same vector search returns different results for `sarah_chen` (US/senior) vs `alex_kim` (EU/associate). Write `tests/test_rls.py`.
**Files:** `backend/src/auth/`, `backend/src/db/rls.py`, `backend/tests/test_rls.py`

### M4 — Agent + Tools (3 hrs)
**Goal:** ReAct agent with 4 tools functional. Can answer questions with citations.
**Test:** CLI test — send a query, get back cited response. Verify tool calls in stdout logs.
**Files:** `backend/src/agent/`, `backend/src/retrieval/vector_store.py`

### M5 — LiteLLM + Kong (2 hrs)
**Goal:** Agent calls route through Kong → LiteLLM → Claude. Fallback chain works.
**Test:** Kill Claude key → verify GPT-4o fallback. Check Kong logs for request/response.
**Files:** `litellm/config.yaml`, `kong/kong.yml`, `litellm/Dockerfile`, `kong/Dockerfile`

### M6 — FastAPI SSE + Frontend (4 hrs)
**Goal:** Full chat UI with streaming, citations, user switcher.
**Test:** Open browser, switch users, ask questions, see citations render with stale badges.
**Files:** `backend/src/api/`, `frontend/`

### M7 — LangFuse + Eval Set (3 hrs)
**Goal:** Every query traced in LangFuse. 50 golden Q&A uploaded. Eval runner works.
**Test:** Open LangFuse UI, see traces. Run `scripts/run_eval.py`, get scores.
**Files:** `backend/src/observability/`, `data/eval/`, `scripts/run_eval.py`

### M8 — CI/CD Eval Gates (2 hrs)
**Goal:** GHA workflow that runs eval on PR and blocks if scores drop.
**Test:** Open a PR that removes citation instructions from system prompt → CI should fail.
**Files:** `.github/workflows/`

### M9 — Terraform + Polish (3 hrs)
**Goal:** Full IaC. Deploy to GCP. README with arch diagram.
**Test:** `terraform apply` → live URL works.
**Files:** `terraform/`, `README.md`, `docs/`

---

**Total estimated: ~24 hours of focused coding.**

Each milestone produces a working, testable increment. No milestone depends on a later one. If you stop at M7, you still have a fully functional, observable, demo-ready system — just without CI/CD and cloud deploy.
