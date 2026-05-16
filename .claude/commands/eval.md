# /eval — Run the eval suite and report scores

Run the golden Q&A evaluation set and report results.

## Steps

1. **Check prerequisites**: Ensure backend is running (or start it), DB is seeded with corpus.

2. **Run eval**:
   ```
   cd backend && python scripts/run_eval.py --eval-set ../data/eval/golden_qa.json --output ../reports/eval_results.json
   ```

3. **Parse results**: Read the output JSON and report:
   - Total questions evaluated
   - Average faithfulness score
   - Average citation accuracy score
   - Average refusal correctness score
   - Number of questions below threshold (citation < 0.95, faithfulness < 0.90)

4. **Show failures**: List any questions that failed thresholds with:
   - Question text
   - Which metric failed
   - Brief reasoning from the judge

5. **Gate decision**:
   - If citation_accuracy >= 0.95 AND faithfulness >= 0.90: "EVAL GATE: PASS"
   - Otherwise: "EVAL GATE: FAIL — deploy would be blocked"

6. **Save report**: Write summary to `reports/eval_summary_{date}.md`

## Usage

- `/eval` — run full eval set
- `/eval --quick` — run first 10 questions only (faster iteration)
- `/eval --category refusal` — run only refusal scenarios
