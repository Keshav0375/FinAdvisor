# FinAdvisor MVP — Compliance-Aware Wealth Advisor Assistant

## The Problem

Wealth advisory firms have thousands of internal documents — product fact sheets, suitability rules, compliance memos, jurisdiction-specific disclosures. Before a client meeting, an advisor needs to check: "Can I recommend this product? What are the disclosure requirements? What does our firm's compliance policy say?"

Today that means keyword-searching SharePoint silos and reading 40-page PDFs. Off-the-shelf chatbots can't be deployed because they:

- Leak client PII into LLM providers
- Hallucinate compliance citations (dangerous in regulated finance)
- Show everyone the same results — a US-licensed associate shouldn't see EU MiFID-only private placements meant for senior advisors

## What FinAdvisor Does

FinAdvisor is a compliance-grade RAG assistant for financial advisors. You ask it a question about a product, a rule, or a client scenario. It:

1. **Checks who you are** — your advisor tier (associate / senior / private-wealth), jurisdiction (US / EU / UK), and licenses (Series-7, IIROC, FCA)
2. **Retrieves only documents you're authorized to see** — enforced at the database level via PostgreSQL Row-Level Security, not application-level filtering
3. **Generates a grounded answer with citations** — every claim links to the source chunk, regulatory reference (e.g., FINRA Rule 2111), and the date that document was last reviewed
4. **Flags stale citations** — if a cited document hasn't been reviewed in 12+ months, the UI shows a warning badge
5. **Redacts PII twice** — once at document ingest (PII never enters the vector store), once at output (catches anything the LLM hallucinates)
6. **Blocks bad prompt changes** — CI/CD eval gates run 50 golden Q&A pairs on every PR; if citation accuracy drops below 95%, the deploy is blocked

## The Demo Flow (What You Show in an Interview)

### Scene 1 — Role-Aware Retrieval

Log in as **Sarah Chen, Senior Advisor, US jurisdiction, Series-7 licensed**. Ask:

> "Is the Vanguard Total Bond Market ETF suitable for a conservative retiree?"

The system retrieves US-jurisdiction product fact sheets and suitability rules that Sarah is authorized to see. The answer includes inline citations: `[1] FINRA Rule 2111 — Suitability, reviewed 2025-03-15`.

Now switch to **Alex Kim, Associate Advisor, EU jurisdiction**. Same question. Completely different results — RLS filters out US-only products, shows MiFID-compliant alternatives, and the associate tier can't see UHNW private placements.

### Scene 2 — PII Protection

Ask: "What's the recommended allocation for John Smith, account #4821-7733?"

The system redacts the name and account number before it ever hits the LLM. The response uses `[CLIENT_NAME_1]` and `[ACCT_1]` tokens. Even if the LLM tries to regurgitate PII from context, the output DLP pass catches it.

### Scene 3 — CI/CD Eval Gate

Open a PR that changes the system prompt (e.g., remove the citation instruction). GitHub Actions runs the eval set. Citation accuracy drops from 97% to 62%. **Build fails. PR gets ❌. You can't merge.**

Show the LangFuse dashboard — trace viewer shows every query's retrieval → tool calls → response → eval scores. Prompt version comparison shows exactly which change caused the regression.

## MVP Components — What Each Piece Does

### Synthetic Corpus (~50 documents)

Fake but realistic financial documents for a fictional firm "Meridian Wealth Partners":

- **Product fact sheets** (20) — ETFs, mutual funds, bonds, private placements. Each tagged with: jurisdiction, required advisor tier, risk level, regulatory references
- **Suitability rules** (10) — firm policies mapping client profiles to product categories. References FINRA/SEC/MiFID rules
- **Compliance memos** (10) — internal policy updates, disclosure requirements, restricted product lists
- **Jurisdiction disclosures** (10) — region-specific regulatory requirements

Each document has metadata: `tier_required`, `jurisdiction`, `regulatory_ref`, `last_reviewed_at`, and has been PII-scrubbed at ingest via GCP DLP.

### Ingest Pipeline

A one-shot script (run locally or as a Cloud Run Job):

1. Reads raw synthetic docs from `data/corpus/`
2. Sends each through GCP DLP API → replaces any PII with stable tokens
3. Chunks documents (recursive text splitter, ~512 tokens per chunk)
4. Generates embeddings via Voyage AI
5. Inserts into PostgreSQL with pgvector — each chunk row carries `tier_required`, `jurisdiction`, `regulatory_ref`, `last_reviewed_at`, `source_doc_id`

### pgvector + Row-Level Security (The Differentiator)

PostgreSQL with the pgvector extension stores embeddings. But the key feature is RLS:

```sql
CREATE POLICY chunk_visibility ON chunks
  USING (
    tier_required <= current_setting('app.user_tier')::int
    AND jurisdiction = ANY(string_to_array(current_setting('app.user_jurisdictions'), ','))
  );
```

Before every query, the FastAPI backend sets session variables from the JWT:

```sql
SET app.user_tier = '3';           -- senior advisor
SET app.user_jurisdictions = 'US'; -- US jurisdiction
```

Then the vector similarity search runs *within* the RLS-filtered set. The database enforces authorization — the application code never sees unauthorized chunks.

### Auth (Mock JWT)

3-4 hardcoded user profiles, switchable via frontend dropdown:

| User | Tier | Jurisdiction | Licenses |
|------|------|-------------|----------|
| Sarah Chen | Senior Advisor (3) | US | Series-7, Series-66 |
| Alex Kim | Associate (1) | EU | MiFID II |
| James Wright | Private Wealth (4) | UK | FCA |
| Priya Sharma | Advisor (2) | US, EU | Series-7, MiFID II |

Each profile generates a signed JWT with these claims. The FastAPI gateway verifies it on every request.

### ReAct Agent (Anthropic SDK)

Single agent with a ReAct loop and 4 typed Pydantic tools:

- **`search_firm_kb`** — vector similarity search on the chunk table (RLS-filtered). Returns top-5 chunks with metadata
- **`lookup_suitability_rule`** — queries suitability rules by product category + client risk profile
- **`lookup_product_factsheet`** — retrieves full product fact sheet by product name/ticker
- **`escalate_to_compliance`** — emits a structured "compliance review needed" flag when the query touches a restricted product class outside the advisor's licenses

The system prompt enforces citation discipline: every claim must reference a retrieved chunk with `[N]` notation including regulatory ref and review date.

### Kong AI Gateway + LiteLLM

**LiteLLM Proxy** — single OpenAI-compatible endpoint that routes to:
- Primary: Claude Sonnet (Anthropic)
- Fallback 1: GPT-4o (OpenAI)
- Fallback 2: Gemini 2.5 (Google)
- Circuit breaker: 3× 5xx in 60s → skip provider for 5 minutes

**Kong AI Gateway** — sits in front of LiteLLM:
- Token budget plugin: per-user token cap, returns 429 with retry-after when exhausted
- PII guard plugin: regex + DLP on request/response payloads

### FastAPI Backend

- JWT verification middleware
- SSE streaming endpoint (`/api/chat/stream`)
- Audit logging (structured JSON to stdout, visible in Cloud Run logs)
- Health check endpoint

### Next.js Frontend

- Chat interface with SSE streaming (token-by-token rendering)
- User profile switcher dropdown (mock auth)
- Citation viewer: inline `[1][2]` markers that expand on hover to show source chunk, doc title, regulatory ref, last-reviewed date
- Stale citation warning: orange badge if `last_reviewed_at` > 12 months ago
- Clean, professional UI (this is finance — no toy styling)

### LangFuse (Self-Hosted)

- Deployed on Cloud Run + Cloud SQL (separate Postgres instance)
- Every agent turn traced: prompt version → retrieval results → tool calls → final response
- Prompt versioning: system prompt stored as a versioned artifact
- Eval datasets: 50 golden Q&A pairs uploaded as a LangFuse dataset
- Score tracking: faithfulness + citation accuracy per trace

### CI/CD Eval Gates (GitHub Actions)

Pipeline on every PR:

1. Lint + typecheck + unit tests
2. Build Docker images
3. Deploy to Cloud Run preview revision
4. Run 50-item eval set against preview
5. LLM-as-judge scores faithfulness + citation accuracy
6. Compare vs baseline (stored in LangFuse)
7. **If citation accuracy < 95% → fail build → PR blocked**
8. On pass: promote revision to serve traffic

### Terraform IaC

Everything defined in Terraform:
- Cloud Run services (frontend, backend, Kong, LiteLLM, LangFuse)
- Cloud SQL instances (app DB with pgvector, LangFuse DB)
- Cloud Storage (corpus source files)
- Secret Manager (API keys)
- IAM bindings
- Artifact Registry (Docker images)

## What This Proves to a Recruiter

1. **You understand authz-aware retrieval** — not just "do RAG", but "do RAG where the database enforces who sees what"
2. **You handle PII in a regulated domain** — two-pass redaction is a real BFSI pattern
3. **You treat prompts as code** — eval gates in CI/CD, version-controlled, rollback-capable
4. **You build observable systems** — every query traced, scored, dashboarded
5. **You deploy for real** — Terraform, Cloud Run, Docker, not a Jupyter notebook
6. **You think about failure modes** — circuit breakers, fallback chains, stale citation warnings
7. **GCP + Azure coverage** — FinAdvisor on GCP, Sentinel on Azure. Both clouds on resume.
