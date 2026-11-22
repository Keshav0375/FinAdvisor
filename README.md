# FinAdvisor

**Compliance-Aware Wealth Advisor RAG Assistant**

A full-stack AI advisory system that provides compliance-scoped financial product recommendations with citations, jurisdiction enforcement, and tier-based access control.

## Architecture

```mermaid
graph TB
    User[Browser] --> Frontend[Vite SPA :3000]
    Frontend --> Backend[FastAPI :8000]
    Backend --> Agent[Agent Orchestrator]
    Agent --> PGVector[(PostgreSQL + pgvector :5432)]
    Agent --> Claude[Anthropic API]
    Backend --> LangFuse[Langfuse Cloud]
    PGVector --> RLS{RLS Policies}

    subgraph Tools
        SearchKB[search_firm_kb]
        LookupSuit[lookup_suitability_rule]
        LookupFact[lookup_product_factsheet]
        Escalate[escalate_to_compliance]
    end

    Agent --> Tools
    Tools --> PGVector
```

### Request Flow

```
User (browser)
  -> Vite React SPA (:3000 dev server) -- renders UI, streams SSE
    -> FastAPI (:8000) -- JWT verify, audit log, SSE endpoint
      -> Agent Orchestrator -- ReAct loop, tool calls
        -> pgvector (RLS-filtered similarity search)
        -> Anthropic API (ANTHROPIC_BASE_URL) -- model ids from Settings
      -> Langfuse Cloud -- trace emitted per turn (when LANGFUSE_ENABLED=true)
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.x async |
| Database | PostgreSQL 16 + pgvector (1024-dim, RLS) |
| Embeddings | Voyage AI (voyage-3) |
| Agent | Anthropic SDK (ReAct loop, 4 tools) |
| LLM access | Anthropic API direct (Kong gateway returns in E2) |
| Observability | Langfuse Cloud (trace per query, ADR-018) |
| Frontend | Vite, React 19, TypeScript, Tailwind CSS, zustand |
| IaC | Terraform (GCP Cloud Run, Cloud SQL) |
| CI | GitHub Actions (lint + eval gate) |

## Key Features

- **Row-Level Security (RLS)** -- PostgreSQL enforces tier and jurisdiction access at the database level. No application-level filtering.
- **Citation System** -- Every claim has a `[N]` citation mapping to retrieved source chunks with regulatory references.
- **Stale Document Warnings** -- Documents older than 12 months get an orange "STALE" badge in the UI.
- **PII Redaction** -- Ingest pipeline redacts PII before embedding. Output pass catches LLM-hallucinated PII.
- **Eval Gate** -- 50 golden Q&A pairs scored by LLM-as-judge. CI blocks if citation_accuracy < 95% or faithfulness < 90%.
- **Direct Anthropic calls** -- The backend calls Anthropic at `ANTHROPIC_BASE_URL` with model ids from `LLM_MODEL_*` settings. Same-provider fallback arrives in E4 and the Kong gateway in E2.

## Mock Users

| User | Tier | Level | Jurisdictions | Licenses |
|------|------|-------|---------------|----------|
| Sarah Chen | senior | 3 | US | Series-7, Series-66 |
| Alex Kim | associate | 1 | EU | MiFID-II |
| James Wright | private_wealth | 4 | UK | FCA |
| Priya Sharma | advisor | 2 | US, EU | Series-7, MiFID-II |

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 20 LTS
- Docker Desktop **running** (for PostgreSQL)
- API keys: `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`

### One-Command Launch

```bash
# 1. Install via Poetry (one time, from project root)
poetry install

# 2. Create backend/.env with your API keys
#    ANTHROPIC_API_KEY=sk-ant-...
#    VOYAGE_API_KEY=pa-...

# 3. Start everything
poetry run finadvisor
```

`poetry run finadvisor` handles everything automatically:
- Checks Docker is running (warns if not)
- Starts PostgreSQL + pgvector container
- Applies database migrations (`jobs migrate`, a no-op when already at the latest revision)
- Ingests 50 synthetic documents with embeddings (if first run, costs ~$0.01 via Voyage AI)
- Starts the FastAPI backend on `:8000`
- Installs frontend deps and starts the Vite dev server on `:3000`
- Connects directly to Anthropic API (no gateway needed)

After first run, starting the app consumes **zero API tokens**. LLM tokens are only used when you ask questions in the chat.

### Sample Questions to Try

1. **Select Sarah Chen** (US Senior, tier 3) and ask:
   > "Is the Meridian Core Bond Fund suitable for a conservative retiree?"
   
   You'll see tool calls (`search_firm_kb`, `lookup_suitability_rule`), citation badges `[1]` `[2]` with FINRA references, and US-only results.

2. **Switch to Alex Kim** (EU Associate, tier 1) and ask:
   > "What US Treasury products can I recommend?"
   
   The agent refuses -- US is outside Alex's jurisdiction. Then ask:
   > "What EU fixed-income products are available?"
   
   This works -- returns EU products with MiFID II citations.

### Manual Setup (Alternative)

```bash
# Start PostgreSQL
docker compose up -d postgres

# Backend setup
cd backend
pip install -e ".[dev]"
python -m src.entrypoint jobs migrate
python scripts/ingest.py
uvicorn src.main:app --reload

# Frontend (new terminal)
cd frontend
npm install
npm run dev

# Open http://localhost:3000
```

### Entrypoints and migrations

The backend image (and `backend/src/entrypoint.py`) has two entrypoints:

```bash
cd backend
python -m src.entrypoint api            # serve the API: 0.0.0.0, one uvicorn process
python -m src.entrypoint jobs migrate   # alembic upgrade head on the configured database
```

`jobs migrate` builds an empty database up to the schema in `backend/db/schema.sql`
(revision `0001`); run it before the first `api`. Any other command exits 2 with usage. In
the image, `docker run fa-backend:test jobs migrate` and `docker run fa-backend:test` (= `api`)
do the same. Migrations live in `backend/migrations/`; the database URL is never in
`alembic.ini`, it comes from the settings below.

Compose Postgres starts empty (no schema is loaded at init). The `migrate` service runs
`jobs migrate` on the runtime image after Postgres is healthy, and `api` starts only once it
exited 0, so one command brings the whole stack up healthy:

```bash
docker compose up -d --wait    # postgres, kafka, migrate (exits 0), api, frontend
```

Host ports bind to `127.0.0.1` and come from `POSTGRES_HOST_PORT`, `KAFKA_HOST_PORT`,
`API_HOST_PORT` and `FRONTEND_HOST_PORT` (defaults 5432, 9092, 8000, 3000; see `.env.example`),
so a busy port or a second project (`docker compose -p other up`) needs only different values.
`up --wait` waits `COMPOSE_WAIT_TIMEOUT_S` seconds (shell environment, default 300; used by the launcher and the compose check) before it gives up.

The `backend` service is now `api`. A stack started before the rename needs
`docker compose down --remove-orphans` once, so containers of the removed services
(`backend`, `langfuse`, `langfuse-db`, `redis`) do not linger.

A database built earlier from `schema.sql` (before migrations) has the tables but no
`alembic_version` table. `jobs migrate` refuses it with exit 1 and never stamps on its own.
After checking that the schema matches `schema.sql`, mark it at revision `0001` once, then run
migrate again:

```bash
cd backend && python -m alembic stamp 0001        # host, same DB settings as the app
docker compose run api python -m alembic stamp 0001   # or in the dev container
```

Settings added for this (env vars, see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | `8000` | Port the `api` entrypoint listens on |
| `DB_CONNECT_TIMEOUT_S` | `10` | Seconds to wait for a database connection before a migration fails |

### Key Commands

```bash
# Quality gate (run after every change)
cd backend
ruff check . --fix && ruff format . && mypy src/ && pytest tests/ -v

# Run eval set
python scripts/run_eval.py --eval-set ../data/eval/golden_qa.json

# Compose acceptance checks (static + runtime; RUNTIME=0 skips the Docker run)
tests/compose/check_compose.sh

# Frontend lint + build
cd frontend
npm run lint && npm run typecheck && npm run test && npm run build

# Stop everything
# Ctrl+C in the finadvisor terminal, then:
docker compose down
```

## Project Structure

```
finadvisor/
  backend/           Python 3.12 FastAPI backend
    src/
      agent/          ReAct orchestrator, tools, system prompt
      api/            FastAPI routes, SSE streaming
      auth/           JWT auth, mock users
      db/             SQLAlchemy models, RLS helpers
      observability/  structlog config, LangFuse tracing
      pii/            PII redaction (GCP DLP + regex fallback)
      retrieval/      Voyage embeddings, vector store
    scripts/          Ingest, corpus gen, eval runner
    tests/            120+ tests (unit, integration, e2e)
  frontend/          Vite + React 19 chat UI (SPA)
    src/
      components/     ChatWindow, CitationPanel, UserSwitcher
      hooks/          useChat (SSE), useUser (zustand)
      lib/            API client, citation parser
  terraform/         GCP infrastructure-as-code
  data/
    corpus/           50 synthetic financial documents
    eval/             50 golden Q&A pairs + judge prompt
```

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) -- Complete design reference
- [TODO.md](TODO.md) -- Task tracker with progress log
- [docs/demo_script.md](docs/demo_script.md) -- Demo recording walkthrough
- [docs/threat_model.md](docs/threat_model.md) -- Security considerations

## License

Private -- Interview project for demonstration purposes.
