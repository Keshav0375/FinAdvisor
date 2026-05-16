# /test-rls — Verify Row-Level Security enforcement

Run the RLS verification test suite to confirm authorization boundaries are enforced.

## Steps

1. **Check prerequisites**: Ensure postgres container is running and test data is seeded.

2. **Run RLS tests**:
   ```
   cd backend && pytest tests/test_rls.py -v --tb=long
   ```

3. **Run auth tests**:
   ```
   cd backend && pytest tests/test_auth.py -v --tb=long
   ```

4. **Manual spot check** (if DB is accessible):
   - Connect as finadvisor_app role
   - SET app.user_tier = '1' and app.user_jurisdictions = 'EU'
   - SELECT count(*) FROM chunks — should only return EU tier-1 chunks
   - SET app.user_tier = '4' and app.user_jurisdictions = 'US,EU,UK,APAC'
   - SELECT count(*) FROM chunks — should return all chunks

5. **Report**: Show which user profiles were tested, chunk counts per profile, and any authorization leakage detected.

## Critical Rule

If ANY test shows a user seeing chunks above their tier or outside their jurisdiction, flag it as a **SECURITY BUG** and do not proceed to the next phase until fixed.
