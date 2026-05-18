# FinAdvisor Demo Script

Step-by-step walkthrough for recording a demo of the FinAdvisor system.

## Prerequisites

- Backend running on `localhost:8000`
- Frontend running on `localhost:3000`
- Database seeded with 50 synthetic documents (114 chunks)
- LangFuse running on `localhost:3030` (optional, for trace visibility)

## Scene 1: Product Suitability (Sarah Chen, US Senior)

**Goal:** Show that the agent retrieves relevant products, cites regulatory references, and uses multiple tools.

1. Open browser to `http://localhost:3000`
2. Select **Sarah Chen** from the user switcher dropdown
3. Note the tier badge (senior) and jurisdiction (US)
4. Type: **"Is the Meridian Core Bond Fund suitable for a conservative retiree?"**
5. Observe:
   - Tool call indicators appear (search_firm_kb, lookup_suitability_rule)
   - Response streams in with `[1]`, `[2]` citation badges
   - Click a citation badge to expand -- shows source document title, regulatory ref (FINRA Rule 2111), last reviewed date
   - No MiFID or FCA references appear (US-only jurisdiction)
6. Type: **"What about the Meridian Private Credit Opportunities fund?"**
7. Observe:
   - Sarah (tier 3) can access this tier-3 product
   - Response cites SEC Regulation D and FINRA Rule 2111
   - Tool calls visible in the streaming UI

**Key talking points:**
- RLS ensures Sarah only sees US products at her tier level or below
- Every factual claim has a traceable citation
- Agent uses multiple tools in a single query (search + suitability lookup)

## Scene 2: Jurisdiction Enforcement (Alex Kim, EU Associate)

**Goal:** Demonstrate that jurisdiction boundaries are enforced by the database, not application code.

1. Switch user to **Alex Kim** in the dropdown
2. Note tier (associate, level 1) and jurisdiction (EU)
3. Type: **"What US Treasury products can I recommend?"**
4. Observe:
   - Agent explains that US products are outside Alex's jurisdiction scope
   - No US-specific product details are returned
   - No FINRA or Series-7 references appear
5. Type: **"What EU fixed-income products are available?"**
6. Observe:
   - Agent retrieves EU Sovereign Bond ETF (tier 1, accessible)
   - Citations reference MiFID II Article 25
   - Only EU-jurisdiction documents appear in results
7. Type: **"Tell me about the Meridian EU Infrastructure Debt Fund."**
8. Observe:
   - This is a tier-3 product -- Alex is tier 1
   - Agent reports that the product information is not available at Alex's access level

**Key talking points:**
- Jurisdiction scoping happens at the database layer via RLS
- Even if the agent tries to search, PostgreSQL returns empty results for out-of-scope data
- Tier enforcement prevents associate-level users from seeing senior/private products

## Scene 3: Refusal and Escalation

**Goal:** Show that the agent correctly refuses out-of-scope requests and escalates when appropriate.

1. Stay as **Alex Kim** (or switch to any user)
2. Type: **"Show me the private wealth tier structured products."**
3. Observe:
   - Agent recognizes this is above Alex's tier
   - Escalation tool call appears (escalate_to_compliance)
   - Response explains access limitations
4. Switch to **Sarah Chen**
5. Type: **"Can you tell me the account balance and SSN for client John Smith?"**
6. Observe:
   - Agent refuses to provide PII or client-specific account information
   - No tool calls made (this is a scope refusal, not a data access issue)
7. Type: **"Can you provide tax advice on capital gains?"**
8. Observe:
   - Agent refuses -- tax advice is outside the system's scope
   - Recommends consulting a tax professional

**Key talking points:**
- The agent knows its boundaries: product info yes, PII no, tax advice no
- Escalation creates a structured compliance record with timestamp and reason
- Refusals are explicit and helpful, not just empty responses

## Scene 4 (Optional): Stale Document Warning

1. Switch to **Sarah Chen**
2. Type: **"What does the Meridian Balanced Income Portfolio offer?"**
3. Observe:
   - Response includes information but flags that the source document is outdated
   - In the UI, the citation shows an orange "STALE" badge
   - Agent recommends verifying with current materials

## Scene 5 (Optional): LangFuse Tracing

1. Open `http://localhost:3030` in a new tab
2. Log in (admin@finadvisor.local / admin123)
3. Navigate to Traces
4. Show the trace from Scene 1:
   - Top level: finadvisor_query trace with user metadata
   - Nested generations: one per ReAct iteration
   - Nested spans: one per tool call with input/output
   - Score: citation_accuracy metric

## Recording Tips

- Use a 1920x1080 window for consistent framing
- Keep the browser dev tools closed unless showing network/SSE
- Pause briefly after each tool call indicator to let viewers read it
- Highlight the citation badges by clicking them open
- Total runtime target: 5-7 minutes for scenes 1-3
