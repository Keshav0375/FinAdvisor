# /check — Run quality gate pipeline

Run the full quality check pipeline for the project. Fix issues automatically where possible.

## Steps

1. **Backend lint** (if backend/src exists):
   ```
   cd backend && ruff check . --fix && ruff format .
   ```
   Report: fixed N issues / clean

2. **Backend typecheck** (if backend/src exists):
   ```
   cd backend && mypy src/
   ```
   Report: N errors / clean

3. **Backend tests** (if backend/tests exists):
   ```
   cd backend && pytest tests/ -v --tb=short
   ```
   Report: N passed, N failed / all green

4. **Frontend lint** (if frontend/src exists):
   ```
   cd frontend && npm run lint
   ```
   Report: N warnings / clean

5. **Frontend build** (if frontend exists):
   ```
   cd frontend && npm run build
   ```
   Report: builds / fails

6. **Docker validation** (if docker-compose.yml exists):
   ```
   docker compose config --quiet
   ```
   Report: valid / invalid

## Output

Show a summary table:

| Step | Status | Details |
|------|--------|---------|
| Lint | pass/fail | ... |
| Types | pass/fail | ... |
| Tests | pass/fail | N passed |
| Frontend | pass/fail | ... |
| Docker | pass/fail | ... |

If any step fails, show the error and suggest a fix. Do not proceed to later steps if an earlier critical step (lint, types) fails — fix it first.
