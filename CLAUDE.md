# FinAdvisor — Claude Code Instructions

> Owner: keshxv | Project: Compliance-Aware Wealth Advisor RAG Assistant
> This file governs all Claude Code behavior in this repository.

---

## Architecture Reference

**Read ARCHITECTURE.md before writing any code.** It is the single source of truth for:
- Service map, port allocations, request flow
- Database schema (pgvector + RLS policies)
- Agent orchestrator design (ReAct loop)
- Tool definitions (Pydantic schemas)
- LLM routing (LiteLLM + Kong)
- Docker Compose topology

**Read TODO.md for current progress.** It tracks what's done, what's next, and verification criteria.

---

## Tech Stack (Locked)

Changes to this stack require explicit human approval:

| Layer | Technology | Version |
|-------|-----------|---------|
| Backend | Python | 3.12 |
| Framework | FastAPI | latest |
| ORM | SQLAlchemy (async) | 2.x |
| Database | PostgreSQL + pgvector | 16 |
| Embeddings | Voyage AI (voyage-3) | 1024 dims |
| Agent | Anthropic SDK (direct) | latest |
| LLM Gateway | LiteLLM + Kong | latest |
| Observability | LangFuse (self-hosted) | 2.x |
| Frontend | Next.js (App Router) | 14 |
| Styling | Tailwind CSS | 3.x |
| State | Zustand | latest |
| IaC | Terraform | 1.5+ |
| Container | Docker Compose | 3.x |
| CI | GitHub Actions | — |
| Linting | ruff + mypy (backend), ESLint (frontend) | — |

---

## Coding Standards (Enforced Always)

### Python (Backend)

- `from __future__ import annotations` on every file
- All functions have full type hints — no `Any` unless interfacing with untyped libs
- Async/await everywhere (no sync DB calls, no sync HTTP)
- Pydantic BaseModel for all cross-boundary data (API, tools, config)
- Dependency injection via FastAPI `Depends()` — no singletons, no global state
- No business logic in API layer — routers call services/agents
- Tools are pure async functions with typed Pydantic input/output
- structlog for all logging (never `print()`, never stdlib `logging`)
- Imports: stdlib → third-party → local (ruff isort handles this)

### TypeScript (Frontend)

- Strict TypeScript (`strict: true` in tsconfig)
- Functional components only (no class components)
- Server Components by default; `"use client"` only when needed
- Hooks for all side effects (no raw useEffect for data fetching)
- Types co-located with components unless shared

### General

- No comments unless explaining WHY (not what)
- No TODO/FIXME in committed code — use TODO.md
- Tests alongside implementation (never ship untested code)
- Commit messages: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`

---

## Error Handling

```python
# Custom exception hierarchy
class FinAdvisorError(Exception): ...
class AuthorizationError(FinAdvisorError): ...
class RetrievalError(FinAdvisorError): ...
class ToolExecutionError(FinAdvisorError): ...
class EvalThresholdError(FinAdvisorError): ...
```

- Catch specific exceptions, never bare `except:`
- structlog context binding: `log = log.bind(user_id=user.sub, trace_id=trace.id)`
- Retry with exponential backoff for external APIs (LLM, embedding, DLP)
- Graceful degradation: if DLP fails → use regex fallback; if Claude fails → use GPT-4o fallback

---

## Testing Strategy

- **Unit tests**: every tool, every utility function, every pydantic model
- **Integration tests**: RLS policies, vector search, agent loop with mocked LLM
- **E2E tests**: full request through FastAPI test client → SSE stream → parsed response
- **Eval tests**: golden Q&A set scored by LLM-as-judge (CI gate)
- Fixtures in `conftest.py` — async session, test DB, seeded data, mock users
- Use `pytest-asyncio` with `auto` mode
- Coverage target: >70% backend, >60% frontend

---

## File Naming Conventions

| Category | Pattern | Example |
|----------|---------|---------|
| ORM models | singular noun | `models.py` → `Document`, `Chunk` |
| Pydantic schemas | singular noun | `schemas.py` → `ChatRequest`, `Citation` |
| Tools | verb_noun | `search_kb.py`, `lookup_suitability.py` |
| API routes | resource name | `chat.py`, `health.py` |
| Tests | `test_` + source name | `test_rls.py`, `test_agent.py` |
| Scripts | verb_noun | `ingest.py`, `generate_corpus.py` |
| Components | PascalCase | `ChatWindow.tsx`, `CitationPanel.tsx` |
| Hooks | `use` + feature | `useChat.ts`, `useUser.ts` |

---

## Key Commands

```bash
# Backend
cd backend && pip install -e ".[dev]"   # Install deps
ruff check . --fix                       # Lint + autofix
ruff format .                            # Format
mypy src/                                # Type check
pytest tests/ -v                         # Run tests
uvicorn src.main:app --reload            # Dev server

# Frontend
cd frontend && npm install               # Install deps
npm run dev                              # Dev server (port 3000)
npm run lint                             # ESLint
npm run build                            # Production build

# Infrastructure
docker compose up                        # All services
docker compose up postgres               # Just DB
python scripts/ingest.py                 # Run ingest pipeline
python scripts/run_eval.py               # Run eval set

# Quality gate (run after every task)
ruff check . --fix && ruff format . && mypy src/ && pytest tests/ -v
```

---

## Non-Negotiable Rules

1. **RLS is the authz layer** — never filter chunks in application code. Always set session vars and let Postgres enforce access.

2. **PII never enters vector store raw** — ingest pipeline redacts BEFORE embedding. Output pass catches LLM hallucinated PII.

3. **Every claim needs a citation** — the agent system prompt enforces `[N]` notation. If the agent makes an unsourced claim, that's a bug.

4. **Eval gate is the deploy gate** — if citation_accuracy < 95% or faithfulness < 90%, the build MUST fail. No exceptions.

5. **Secrets never in code** — all API keys via env vars. `.env` is gitignored. Use `.env.example` for documentation.

6. **Cost awareness** — before any bulk API call (embedding 300 chunks, running 50 evals), calculate cost and flag to user.

---

## When Stuck

1. Check ARCHITECTURE.md — the answer is usually there
2. Check TODO.md — verify you're working on the right task
3. Check the Anthropic SDK docs for agent patterns
4. Check LiteLLM docs for routing config
5. Ask the user before implementing anything ambiguous
6. Never guess at financial regulations — use only what's in the synthetic corpus

---

## Session Start Ritual

1. Read TODO.md — find current phase and next task
2. Run `/progress` — confirm completion percentage
3. Suggest next task to implement
4. Get human approval before starting
