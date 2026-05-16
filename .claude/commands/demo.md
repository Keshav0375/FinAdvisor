# /demo — Run through the interview demo scenarios

Execute the 3 demo scenes from mvp.md to verify the system is demo-ready.

## Scene 1 — Role-Aware Retrieval

1. Set user to sarah_chen (US/senior/tier-3)
2. Send: "Is the Vanguard Total Bond Market ETF suitable for a conservative retiree?"
3. Verify: response cites US product factsheets, FINRA Rule 2111, inline citations present
4. Switch to alex_kim (EU/associate/tier-1)
5. Send same question
6. Verify: different results (no US products), MiFID references, no UHNW products

## Scene 2 — PII Protection

1. Set user to sarah_chen
2. Send: "What's the recommended allocation for John Smith, account #4821-7733?"
3. Verify: response uses [CLIENT_NAME_1] and [ACCT_1] tokens, no raw PII in output

## Scene 3 — Stale Citation Warning

1. Find a document with last_reviewed_at > 12 months ago
2. Ask a question that retrieves it
3. Verify: response includes stale warning badge text

## Report

For each scene:
- PASS/FAIL
- Actual response snippet
- Any issues found

Final: "DEMO READY: YES/NO" with list of any blocking issues.
