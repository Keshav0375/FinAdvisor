# /review — Code review focused on security and compliance patterns

Review the current uncommitted changes (or $ARGUMENTS branch diff) for FinAdvisor-specific concerns.

## Checks

### 1. RLS Safety
- Grep for raw SQL queries that don't go through `set_rls_context` first
- Grep for direct chunk table access without session var setup
- Verify no application-level filtering that should be RLS-enforced

### 2. PII Handling
- Grep for raw text storage without redaction pass
- Verify ingest path: raw → DLP/regex → store
- Verify output path: LLM response → DLP/regex → stream to user
- Check for logging that might expose PII (user names, account numbers in log messages)

### 3. Auth Boundaries
- Verify all API endpoints have `Depends(get_current_user)`
- Check for endpoints that bypass auth
- Verify user context flows to agent/tools (not just auth check at edge)

### 4. Type Safety
- Run `mypy src/` — report any errors
- Check Pydantic models match API contracts in ARCHITECTURE.md
- Verify tool input/output schemas match Anthropic format

### 5. Citation Integrity
- Check system prompt still enforces citation rules
- Verify tool results include source_title, regulatory_ref, last_reviewed_at
- Confirm stale date logic (12-month threshold) is correct

### 6. Secret Safety
- Grep for hardcoded API keys, passwords, or tokens
- Verify .env is gitignored
- Check no secrets in Docker build args or committed configs

## Output

Show results as a checklist:
- [x] RLS: all queries go through session context
- [ ] PII: found logging of user.name in chat.py:45

Flag severity: CRITICAL (blocks merge) / WARNING (should fix) / INFO (suggestion).
