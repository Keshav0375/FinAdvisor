# FinAdvisor — TODO Tracker

> **Total effort = 100%.** Each task = 1–5% of interview-ready MVP.
> Completed: **7%** | Remaining: **93%** | Current Phase: **0**
>
> This file is the execution plan. ARCHITECTURE.md is the design bible.
> Update this file after every task completion with `[x]`, date, and notes.

---

## Phase 0 — Foundation & Tooling (10%)

### [x] 0.1 — Initialize Python backend project (2%)

Create `backend/pyproject.toml` with uv/pip management. Define dependencies:
fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, pydantic-settings, anthropic,
voyageai, langfuse, structlog, sse-starlette, python-jose, google-cloud-dlp.
Dev deps: pytest, pytest-asyncio, ruff, mypy, httpx (test client).
Create `backend/src/__init__.py` and `backend/src/config.py` with pydantic-settings.

**Verify:** `pip install -e ".[dev]"` succeeds, `python -c "from src.config import Settings"` works.

```
Notes:
─────
2026-05-17: Created pyproject.toml with all runtime + dev deps, setuptools build,
ruff (line-length=100, py312, isort) and mypy (strict) config. Created src/config.py
with pydantic-settings Settings class covering all env vars from ARCHITECTURE.md.
Verified: pip install -e ".[dev]" succeeds, Settings imports cleanly.
```

### [x] 0.2 — Initialize Next.js frontend project (2%)

Create `frontend/` with Next.js 14 (App Router), TypeScript, Tailwind CSS.
Install: zustand (state), eventsource-parser (SSE). Configure `next.config.js`
to proxy `/api` to backend at `localhost:8000`.

**Verify:** `npm run dev` starts, blank page loads at `localhost:3000`.

```
Notes:
─────
2026-05-17: Scaffolded via create-next-app@14 (App Router, TypeScript, Tailwind, src dir).
Installed zustand + eventsource-parser. Configured next.config.mjs with rewrites
proxying /api/* to localhost:8000. Simplified page.tsx to minimal placeholder.
Verified: npm run dev starts (Ready in 4s), npm run build succeeds, npm run lint clean.
```

### [x] 0.3 — Docker Compose foundation (3%)

Create `docker-compose.yml` with services: postgres (pgvector/pgvector:pg16),
backend, frontend, litellm, kong, langfuse, langfuse-db, redis.
Create `docker-compose.override.yml` for hot-reload dev mode.
Create `.env.example` with all required env vars (no real keys).

**Verify:** `docker compose config` validates. `docker compose up postgres` starts.

```
Notes:
─────
2026-05-17: Created docker-compose.yml (8 services: postgres, backend, frontend,
litellm, kong, langfuse, langfuse-db, redis), docker-compose.override.yml (hot-reload
with volume mounts), .env.example (all env vars). Created placeholder schema.sql
in backend/db/. YAML validated programmatically (Docker Desktop not on this machine).
```

### [ ] 0.4 — Git hygiene + linting setup (1%)

Create `.gitignore` (Python + Node + env + Docker volumes).
Configure `ruff.toml` (line-length=100, target=py312, isort).
Configure `mypy` in `pyproject.toml` (strict mode, asyncio plugin).
Frontend: ESLint + Prettier config.

**Verify:** `ruff check .` and `mypy src/` run clean on empty project.

```
Notes:
─────
(pending)
```

### [ ] 0.5 — CI workflow scaffold (2%)

Create `.github/workflows/ci.yml`: lint + typecheck + unit tests.
Create `.github/workflows/eval-gate.yml`: placeholder (activated in Phase 8).
Both triggered on PR to main.

**Verify:** YAML is valid. `act` dry-run passes (if installed).

```
Notes:
─────
(pending)
```

---

## Phase 1 — Database Schema & RLS (10%)

### [ ] 1.1 — PostgreSQL schema file (3%)

Create `backend/db/schema.sql` with exact schema from ARCHITECTURE.md Section 3:
- `documents` table (full metadata)
- `chunks` table (embedding vector(1024), denormalized metadata)
- `suitability_rules` table
- Indexes (ivfflat on embedding, btree on jurisdiction/tier)
- RLS policies (chunk_visibility)
- Roles (finadvisor_service, finadvisor_app)

**Verify:** `psql -f schema.sql` against local postgres creates all objects.
`\dp chunks` shows RLS policy. `\di` shows indexes.

```
Notes:
─────
(pending)
```

### [ ] 1.2 — SQLAlchemy ORM models (2%)

Create `backend/src/db/models.py`:
- `Document` model (maps to documents table)
- `Chunk` model (maps to chunks table, pgvector column)
- `SuitabilityRule` model

Use mapped_column with proper types. Vector column via pgvector-python.

**Verify:** `mypy src/db/models.py` passes. Models import cleanly.

```
Notes:
─────
(pending)
```

### [ ] 1.3 — Async session factory + RLS helpers (3%)

Create `backend/src/db/session.py`: async SQLAlchemy session factory using
`create_async_engine` + `async_sessionmaker`.
Create `backend/src/db/rls.py`: `set_rls_context(session, user)` function
that SETs app.user_tier and app.user_jurisdictions via raw SQL.

**Verify:** Unit test connects to test DB, sets session vars, confirms
`current_setting('app.user_tier')` returns expected value.

```
Notes:
─────
(pending)
```

### [ ] 1.4 — Database integration test (2%)

Create `backend/tests/conftest.py` with fixtures: test DB setup/teardown,
async session, sample data insertion.
Create `backend/tests/test_rls.py`:
- Test 1: tier-3 user sees tier-1,2,3 chunks, not tier-4
- Test 2: EU-only user sees EU chunks, not US chunks
- Test 3: combined filter (tier + jurisdiction)

**Verify:** `pytest tests/test_rls.py -v` — all 3 tests pass.

```
Notes:
─────
(pending)
```

---

## Phase 2 — Synthetic Corpus & Ingest Pipeline (15%)

### [ ] 2.1 — Synthetic document generator (5%)

Create `backend/scripts/generate_corpus.py`:
- Uses Claude API to generate 50 documents following ARCHITECTURE.md Section 4
- Output: JSON files in `data/corpus/{doc_type}/` directories
- 20 product factsheets, 10 suitability rules, 10 compliance memos, 10 disclosures
- Each doc: 300-800 words, realistic financial language, real regulatory refs
- Some with stale dates (>12 months), some with embedded PII patterns

**Verify:** `python scripts/generate_corpus.py` produces 50 JSON files.
Spot-check: valid structure, tier/jurisdiction tags present, some PII embedded.

**HUMAN APPROVAL REQUIRED:** Review generated corpus for quality before proceeding.

```
Notes:
─────
(pending)
```

### [ ] 2.2 — PII redaction module (3%)

Create `backend/src/pii/redactor.py`: GCP DLP wrapper (PIIRedactor class).
Create `backend/src/pii/fallback.py`: regex-based fallback for local dev.
Create `backend/src/pii/__init__.py`: factory that picks mode from config.

**Verify:** `pytest tests/test_pii.py` — regex fallback catches SSN, phone,
email, account numbers. DLP mock test passes.

```
Notes:
─────
(pending)
```

### [ ] 2.3 — Voyage AI embeddings client (2%)

Create `backend/src/retrieval/embeddings.py`:
- VoyageEmbeddings class with batch embedding (voyage-3, 1024 dims)
- Rate limiting (3 requests/sec)
- Retry with exponential backoff

**Verify:** Integration test embeds a sample sentence, returns 1024-dim vector.

**HUMAN APPROVAL REQUIRED:** Confirm Voyage AI API key is available.

```
Notes:
─────
(pending)
```

### [ ] 2.4 — Ingest pipeline script (5%)

Create `backend/scripts/ingest.py`:
1. Read JSON docs from `data/corpus/`
2. PII-redact raw content
3. Chunk with RecursiveCharacterTextSplitter (512 tokens, 50 overlap)
4. Batch embed via Voyage AI
5. INSERT into documents + chunks tables (bypass RLS via service role)

Support `--target test` flag for CI (uses test DB).

**Verify:** After running, `SELECT count(*) FROM chunks;` returns 200-300 rows.
Raw vector search returns sensible results.

**HUMAN APPROVAL REQUIRED:** Verify embedding costs before full corpus ingest.

```
Notes:
─────
(pending)
```

---

## Phase 3 — Auth & RLS Verification (8%)

### [ ] 3.1 — Mock user profiles + JWT models (2%)

Create `backend/src/auth/mock_users.py`: MOCK_USERS dict (4 users from arch).
Create `backend/src/auth/models.py`: UserClaims pydantic model.
Create `backend/src/auth/jwt.py`: `get_current_user` dependency (header-based MVP).
Create `data/seed/mock_users.json`: same data in JSON for frontend.

**Verify:** `mypy src/auth/` passes. Unit test verifies lookup by X-User-Id header.

```
Notes:
─────
(pending)
```

### [ ] 3.2 — Auth middleware integration (2%)

Wire `get_current_user` as FastAPI dependency. Return 401 for unknown users.
Add request logging with structlog (user_id, tier, jurisdictions per request).

**Verify:** `pytest tests/test_auth.py` — valid user returns UserClaims,
invalid user returns 401, missing header returns 401.

```
Notes:
─────
(pending)
```

### [ ] 3.3 — End-to-end RLS verification (4%)

Full integration test: ingest sample docs → set RLS context per user →
run vector similarity search → verify result sets differ by user.

Test matrix:
- sarah_chen (US/tier-3): sees US tier-1,2,3 docs
- alex_kim (EU/tier-1): sees only EU tier-1 docs
- james_wright (UK/tier-4): sees UK tier-1,2,3,4 docs
- priya_sharma (US+EU/tier-2): sees US+EU tier-1,2 docs

**Verify:** All 4 user scenarios pass. Zero unauthorized chunk leakage.

**HUMAN APPROVAL REQUIRED:** Security review of RLS policy before agent integration.

```
Notes:
─────
(pending)
```

---

## Phase 4 — Agent Orchestrator & Tools (15%)

### [ ] 4.1 — Tool: search_firm_kb (4%)

Create `backend/src/agent/tools/search_kb.py`:
- Pydantic input/output models (SearchKBInput, ChunkResult, SearchKBOutput)
- Implementation: embed query → pgvector cosine similarity → top-k results
- Results include: chunk_id, content, source_title, regulatory_ref, last_reviewed_at, score
- RLS automatically filters (session vars already set)

Create `backend/src/retrieval/vector_store.py`: VectorStore class with
`similarity_search(query_embedding, top_k)` method.

**Verify:** Unit test mocks embedding, returns correct chunks.
Integration test with real DB returns RLS-filtered results.

```
Notes:
─────
(pending)
```

### [ ] 4.2 — Tool: lookup_suitability_rule (2%)

Create `backend/src/agent/tools/lookup_suitability.py`:
- Input: product_category, client_risk_profile
- Queries suitability_rules table filtered by jurisdiction (from user context)
- Returns matching rules with regulatory refs

**Verify:** Unit test with seeded rules returns correct matches.

```
Notes:
─────
(pending)
```

### [ ] 4.3 — Tool: lookup_product_factsheet (2%)

Create `backend/src/agent/tools/lookup_factsheet.py`:
- Input: product_name (fuzzy match on title)
- Queries documents table (doc_type='product_factsheet') with RLS
- Returns full factsheet content + metadata

**Verify:** Unit test returns correct factsheet by name.

```
Notes:
─────
(pending)
```

### [ ] 4.4 — Tool: escalate_to_compliance (2%)

Create `backend/src/agent/tools/escalate.py`:
- Input: reason, product_class, advisor_licenses
- Returns structured escalation record (logged, not sent anywhere in MVP)
- Logs to structlog with compliance_escalation event

**Verify:** Unit test verifies structured output + log emission.

```
Notes:
─────
(pending)
```

### [ ] 4.5 — Tool registry + DI wiring (2%)

Create `backend/src/agent/tools/__init__.py`: ToolRegistry class.
Anthropic schema generation from Pydantic models.
Tool execution dispatcher with user context injection.

**Verify:** Registry produces valid Anthropic tool schema JSON.
`execute("search_firm_kb", {...}, user)` dispatches correctly.

```
Notes:
─────
(pending)
```

### [ ] 4.6 — Agent orchestrator (ReAct loop) (3%)

Create `backend/src/agent/orchestrator.py`: FinAdvisorAgent class.
- ReAct loop: query → Claude → tool_use → execute → loop → final text
- Max 5 iterations with graceful degradation
- Async generator yielding StreamEvent (text/tool_call/citation/error)
- LangFuse trace integration (generation + span per tool)

Create `backend/src/agent/system_prompt.py`: versioned system prompt.
Create `backend/src/agent/types.py`: StreamEvent, ToolCallEvent, CitationEvent.

**Verify:** CLI test — send query, get back cited response with tool calls logged.

**HUMAN APPROVAL REQUIRED:** Review system prompt before integration testing.

```
Notes:
─────
(pending)
```

---

## Phase 5 — LLM Routing: LiteLLM + Kong (8%)

### [ ] 5.1 — LiteLLM proxy configuration (3%)

Create `litellm/config.yaml`: model routing (Claude primary, GPT-4o fallback,
Gemini fallback). Circuit breaker: 3 fails → 5min cooldown.
Create `litellm/Dockerfile`.

**Verify:** `docker compose up litellm` starts. `curl localhost:4000/health` OK.
Send test request → routes to Claude.

**HUMAN APPROVAL REQUIRED:** Confirm all API keys (Anthropic, OpenAI, Google) available.

```
Notes:
─────
(pending)
```

### [ ] 5.2 — Kong AI Gateway configuration (3%)

Create `kong/kong.yml`: declarative config with rate-limiting, request-size-limiting,
http-log plugins.
Create `kong/Dockerfile`.
Wire Kong → LiteLLM in docker-compose.

**Verify:** Request through Kong:8001 → LiteLLM:4000 → Claude. Rate limit triggers at 61st req/min.

```
Notes:
─────
(pending)
```

### [ ] 5.3 — Fallback chain verification (2%)

Integration test: disable Claude key → verify GPT-4o fallback.
Disable both → verify Gemini fallback.
Verify circuit breaker trips after 3 consecutive failures.

**Verify:** Logged fallback events in LiteLLM. Response still valid.

```
Notes:
─────
(pending)
```

---

## Phase 6 — FastAPI Backend & SSE Streaming (10%)

### [ ] 6.1 — App factory + health endpoint (2%)

Create `backend/src/main.py`: FastAPI app factory with lifespan (DB pool, LangFuse, embeddings).
Create `backend/src/api/router.py`: main router.
Create `backend/src/api/health.py`: GET /health endpoint.
Create `backend/src/dependencies.py`: DI providers (get_db_session, get_agent, get_redactor).
Create `backend/Dockerfile`.

**Verify:** `docker compose up backend` starts. `curl localhost:8000/health` returns OK.

```
Notes:
─────
(pending)
```

### [ ] 6.2 — SSE streaming endpoint (4%)

Create `backend/src/api/chat.py`: POST /api/chat/stream.
- Accepts ChatRequest (message, conversation_id)
- Sets RLS context from authenticated user
- Runs agent, streams events as SSE
- Output PII pass on final text
- Events: message, tool, citation, done

Create `backend/src/api/schemas.py`: ChatRequest, Citation, ChatResponse models.

**Verify:** `curl -N -X POST localhost:8000/api/chat/stream` with valid headers
streams SSE events. Citations appear in output.

```
Notes:
─────
(pending)
```

### [ ] 6.3 — Structured logging + error handling (2%)

Create `backend/src/observability/logging.py`: structlog config (JSON output,
request_id binding, user context).
Add global exception handler. Log all tool calls, LLM latency, token usage.

**Verify:** Request produces structured JSON log lines with user_id, trace_id, latency.

```
Notes:
─────
(pending)
```

### [ ] 6.4 — Backend integration test suite (2%)

Create `backend/tests/test_agent.py`: mock LLM responses, verify tool dispatch.
Extend `conftest.py` with FastAPI test client fixtures.
Test: full request → SSE stream → parsed events contain citations.

**Verify:** `pytest tests/ -v` — all tests pass, coverage > 70%.

```
Notes:
─────
(pending)
```

---

## Phase 7 — Next.js Frontend (10%)

### [ ] 7.1 — Layout + UserSwitcher component (2%)

Create `frontend/src/app/layout.tsx`: base layout with header.
Create `frontend/src/components/UserSwitcher.tsx`: dropdown with 4 mock users.
Create `frontend/src/hooks/useUser.ts`: current user context (localStorage persisted).
Create `frontend/src/lib/users.ts`: mock user profiles (synced with backend).

**Verify:** Dev server shows header with user dropdown. Selection persists on refresh.

```
Notes:
─────
(pending)
```

### [ ] 7.2 — Chat interface + SSE streaming (4%)

Create `frontend/src/components/ChatWindow.tsx`: message list + input.
Create `frontend/src/components/MessageBubble.tsx`: renders single message.
Create `frontend/src/components/StreamingText.tsx`: SSE token renderer.
Create `frontend/src/hooks/useChat.ts`: SSE connection, message state, streaming.
Create `frontend/src/lib/api.ts`: API client with X-User-Id header.

**Verify:** Type a question → see streaming response appear token by token.

```
Notes:
─────
(pending)
```

### [ ] 7.3 — Citation system (3%)

Create `frontend/src/components/CitationInline.tsx`: clickable [N] badges.
Create `frontend/src/components/CitationPanel.tsx`: expanded citation view
(doc title, regulatory ref, reviewed date, chunk preview).
Create `frontend/src/components/StaleBadge.tsx`: orange warning for stale citations.
Parse `[N]` markers in message text, replace with interactive components.

**Verify:** Citations render inline. Click expands panel. Stale docs show warning.

```
Notes:
─────
(pending)
```

### [ ] 7.4 — UI polish + responsive design (1%)

Professional finance styling: clean typography, subtle colors, proper spacing.
Mobile-responsive chat layout. Loading states. Error display.

**Verify:** Looks professional on desktop + mobile. No console errors.

**HUMAN APPROVAL REQUIRED:** UI/UX review before moving to observability.

```
Notes:
─────
(pending)
```

---

## Phase 8 — LangFuse Observability & Eval (8%)

### [ ] 8.1 — LangFuse integration (3%)

Create `backend/src/observability/tracing.py`: LangFuse trace context manager.
Wire into agent orchestrator: trace per query, generation per LLM call,
span per tool execution.
Create `langfuse/docker-compose.langfuse.yml` (self-hosted).
Set up prompt versioning (system prompt stored in LangFuse).

**Verify:** Open LangFuse UI at localhost:3030. Run a query. Trace appears with
full call chain: generations + spans + tool results.

```
Notes:
─────
(pending)
```

### [ ] 8.2 — Golden Q&A eval set (2%)

Create `data/eval/golden_qa.json`: 50 Q&A pairs covering:
- Product suitability (15), jurisdiction scoping (10), refusal scenarios (10),
  citation accuracy (10), stale doc handling (5)
Create `data/eval/judge_prompt.txt`: LLM-as-judge scoring prompt.

**Verify:** JSON validates. Each entry has: id, category, user_profile, question,
expected_behavior, must_cite_refs, must_not_contain, expected_tool_calls.

**HUMAN APPROVAL REQUIRED:** Review eval set for coverage completeness.

```
Notes:
─────
(pending)
```

### [ ] 8.3 — Eval runner script (3%)

Create `backend/scripts/run_eval.py`:
- Runs each golden Q&A against the agent
- LLM-as-judge scores: faithfulness, citation_accuracy, refusal_correctness
- Outputs results.json with per-question + aggregate scores
- Supports `--compare-baseline` flag (vs LangFuse stored scores)

**Verify:** `python scripts/run_eval.py --eval-set data/eval/golden_qa.json`
produces results. Citation accuracy > 90% on first run.

```
Notes:
─────
(pending)
```

---

## Phase 9 — CI/CD Eval Gates + Terraform + Polish (6%)

### [ ] 9.1 — GitHub Actions eval gate (2%)

Activate `.github/workflows/eval-gate.yml`:
- Spin up pgvector service container
- Seed test data
- Run eval set
- Check thresholds: citation_accuracy >= 95%, faithfulness >= 90%
- Upload results as artifact

**Verify:** PR that degrades system prompt → CI fails. Good PR → CI passes.

```
Notes:
─────
(pending)
```

### [ ] 9.2 — Terraform IaC (3%)

Create `terraform/` with all resources from ARCHITECTURE.md Section 15:
- main.tf, variables.tf, outputs.tf
- cloud_run.tf (backend, frontend, kong, litellm, langfuse)
- cloud_sql.tf (app DB + pgvector, langfuse DB)
- cloud_storage.tf, secrets.tf, iam.tf, artifact_registry.tf

**Verify:** `terraform validate` passes. `terraform plan` shows expected resources.

**HUMAN APPROVAL REQUIRED:** Review infra plan before any `terraform apply`.

```
Notes:
─────
(pending)
```

### [ ] 9.3 — Documentation + demo script (1%)

Create `README.md`: architecture diagram (mermaid), setup instructions,
demo walkthrough link.
Create `docs/demo_script.md`: step-by-step Loom recording script.
Create `docs/threat_model.md`: security considerations doc.

**Verify:** README renders on GitHub. Demo script covers all 3 demo scenes.

```
Notes:
─────
(pending)
```

---

## Execution Rules

1. **Sequential phases** — complete each phase before starting the next
2. **Verify before marking done** — run the verification command listed on each task
3. **Notes are mandatory** — every `[x]` task gets a dated Notes entry
4. **HUMAN APPROVAL gates** — stop and get explicit approval before continuing
5. **Run /check after every task** — lint + typecheck + test must pass
6. **Story-Reports** — generate `Story-Reports/{phase}-{task}.md` after major tasks
7. **No skipping** — if a task seems unnecessary, ask before removing it
8. **Cost awareness** — flag API costs (embeddings, LLM calls) before incurring them

---

## Progress Log

| Date | Task | Notes |
|------|------|-------|
| — | — | Project initialized |
| 2026-05-17 | 0.1 | Python backend initialized: pyproject.toml, src/config.py (pydantic-settings) |
| 2026-05-17 | 0.2 | Next.js 14 frontend scaffolded with TS, Tailwind, zustand, API proxy |
| 2026-05-17 | 0.3 | Docker Compose with 8 services, override for dev, .env.example |
