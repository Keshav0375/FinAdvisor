# FinAdvisor Threat Model

Security considerations for the Compliance-Aware Wealth Advisor RAG system.

## 1. Data Access Control

### Row-Level Security (RLS)

**Threat:** Unauthorized access to financial documents above a user's tier or outside their jurisdiction.

**Mitigation:**
- PostgreSQL RLS policies enforce access at the database level -- not in application code
- Every query sets session variables (`app.user_tier`, `app.user_jurisdictions`) via `SET ROLE finadvisor_app` before executing
- The `finadvisor_app` role has RLS policies applied; the superuser `finadvisor` role is never used for application queries
- Chunks table has denormalized `tier_required` and `jurisdiction` columns to avoid JOINs in the RLS hot path
- Integration tests verify that different users see different result sets for the same vector search

**Residual risk:** RLS bypass if application code fails to call `set_rls_context()` before querying. Mitigated by the `get_rls_session` dependency injection pattern in FastAPI.

### Tier Hierarchy

| Level | Tier | Access |
|-------|------|--------|
| 1 | associate | Tier 1 documents only |
| 2 | advisor | Tier 1-2 documents |
| 3 | senior | Tier 1-3 documents |
| 4 | private_wealth | All documents |

## 2. PII Protection

### Ingest Pipeline

**Threat:** Personally identifiable information embedded in financial documents gets stored in the vector database.

**Mitigation:**
- PII redaction runs BEFORE embedding in the ingest pipeline
- Two modes: GCP DLP (production) and regex fallback (development)
- Regex patterns cover: SSN, phone numbers, email addresses, account numbers
- Redacted text replaces PII with `[REDACTED]` markers

### Output Redaction

**Threat:** LLM hallucinates or reconstructs PII in its responses.

**Mitigation:**
- Output redaction pass on every SSE text event before sending to the client
- Uses the same regex patterns as ingest
- Configured via `PII_MODE` environment variable

**Residual risk:** Novel PII patterns not covered by regex (e.g., foreign ID formats). Mitigated in production by using GCP DLP which has broader pattern coverage.

## 3. Authentication and Authorization

### Current Implementation (Mock)

**Threat:** Unauthorized API access.

**Mitigation:**
- `X-User-Id` header maps to predefined mock user profiles
- Unknown user IDs return 401
- Each user profile has fixed tier, jurisdictions, and licenses

### Production Considerations

- Replace mock auth with JWT verification (RS256)
- Use OIDC provider (Auth0, Okta) for token issuance
- JWT claims should carry `tier_level`, `jurisdictions`, `licenses`
- Token expiry and refresh token rotation
- API key rotation for service-to-service communication

## 4. LLM Security

### Prompt Injection

**Threat:** Malicious user input manipulates the system prompt or tool behavior.

**Mitigation:**
- System prompt is versioned and stored separately (LangFuse prompt management)
- User input is always in the `user` message role, never concatenated into the system prompt
- Tool inputs are validated via Pydantic models before execution
- The ReAct loop has a `max_iterations` limit (default 5) to prevent infinite loops

### Data Exfiltration via Tools

**Threat:** Crafted queries that cause the agent to leak data from tool results.

**Mitigation:**
- RLS prevents tools from accessing unauthorized data regardless of query
- Tool outputs are structured (Pydantic models) and serialized as JSON
- The escalation tool creates audit records for suspicious requests

### Model Fallback Chain

**Threat:** Primary model unavailability or degraded quality.

**Mitigation:**
- LiteLLM routes through Claude -> GPT-4o -> Gemini
- Circuit breaker pattern prevents cascading failures
- Kong gateway enforces rate limits per user
- All model calls are traced in LangFuse for quality monitoring

## 5. Infrastructure Security

### Secrets Management

**Threat:** API keys and database credentials leaked in code or logs.

**Mitigation:**
- All secrets stored in environment variables, never in code
- `.env` files are gitignored
- Production: GCP Secret Manager with IAM-scoped access
- Terraform creates Secret Manager resources; values populated out-of-band
- structlog configured to avoid logging sensitive fields

### Network Security

**Threat:** Unauthorized access to internal services.

**Mitigation (Production):**
- Cloud Run services communicate via internal URLs (not public internet)
- Kong gateway is the only public-facing entry point for LLM traffic
- Cloud SQL uses Cloud SQL Proxy (Unix socket) -- no public IP
- LangFuse is internal-only (not exposed to public internet)

### Container Security

**Threat:** Vulnerable base images or dependency supply chain attacks.

**Mitigation:**
- Python 3.12-slim base image
- Dependencies pinned in `pyproject.toml`
- GitHub Actions CI runs on every PR
- Docker images built from Dockerfiles in the repo (not pulled from arbitrary registries)

## 6. Eval and Quality Gates

### Eval Gate

**Threat:** Degraded model quality or broken citation logic ships to production.

**Mitigation:**
- 50 golden Q&A pairs cover 5 categories: product suitability, jurisdiction scoping, refusal, citation accuracy, stale document handling
- LLM-as-judge scores faithfulness, citation_accuracy, refusal_correctness
- CI blocks deployment if citation_accuracy < 95% or faithfulness < 90%
- Results uploaded as GitHub Actions artifacts for audit trail

### Stale Document Handling

**Threat:** Outdated financial information presented as current.

**Mitigation:**
- Documents have `last_reviewed_at` dates
- UI displays orange "STALE" badge for documents older than 12 months
- Agent system prompt instructs to flag stale sources
- Eval set includes 5 stale document test cases

## 7. Compliance Considerations

### Audit Trail

- Every query is traced in LangFuse: user ID, tool calls, sources retrieved, response text
- Escalation tool creates structured compliance records with timestamps
- HTTP request logging includes user ID and request ID for correlation
- structlog JSON output enables log aggregation and search

### Regulatory References

- All citations map to specific regulatory frameworks (FINRA, MiFID II, FCA COBS, SEC, MSRB)
- The system only uses information from the synthetic corpus -- it never invents regulatory guidance
- The eval gate specifically tests that regulatory references are accurate and not fabricated

### Data Residency

- Production deployment uses GCP region configuration (default: us-central1)
- Terraform variables allow region selection per compliance requirements
- LangFuse self-hosted ensures trace data stays within the deployment boundary
