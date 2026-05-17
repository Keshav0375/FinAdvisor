# /implement — Execute a TODO.md task

Execute the task matching "$ARGUMENTS" from TODO.md.

## Workflow

1. **Locate task**: Find the task in TODO.md that matches the argument (task number like "0.1" or keyword match). If ambiguous, list candidates and ask which one.

2. **Read context**: Read ARCHITECTURE.md sections relevant to this task. Identify exact file paths, schemas, and contracts needed.

3. **Plan**: State in 2-3 sentences what you will build and which files you will create/modify. Wait for approval if the task is marked "HUMAN APPROVAL REQUIRED".

4. **Implement**: Write the code following CLAUDE.md standards:
   - `from __future__ import annotations` on every Python file
   - Full type hints, async/await, Pydantic models
   - structlog for logging
   - No comments unless explaining WHY

5. **Quality gate**: Run the full check pipeline:
   ```
   ruff check . --fix && ruff format . && mypy src/ && pytest tests/ -v
   ```
   Fix any issues. Do not proceed until all pass.

6. **Verify**: Run the specific verification command listed on the task in TODO.md. Confirm it passes.

7. **Update TODO.md**: 
   - Change `[ ]` to `[x]` on the task
   - Add dated Notes entry with what was built and key decisions
   - Update the "Completed: X%" counter at the top
   - Add entry to Progress Log table at bottom

8. **Generate Story Report**: Create `Story-Reports/{phase}.{task_num}-{short_name}.md` with:
   - Problem: what this task solves (1-2 sentences)
   - Implementation: what was built (bullet list of files + purpose)
   - Decisions: any non-obvious choices made and why
   - Verification: what was tested and the result
   - Next: what task follows logically

9. **Commit**: Stage relevant files and commit with appropriate prefix:
   ```
   feat: {short description of what was built}
   ```

10. **Handoff**: After the commit, read TODO.md to find the next uncompleted task. Present a one-click prompt to the user:
    - Show the next task number and title
    - Option 1: "Yes, run next task" — if selected, immediately invoke `/implement {next_task_number}`
    - Option 2: "No, stop here"
    - This keeps the pipeline moving without the user having to type `/implement X.Y` each time

## Rules

- Never skip the quality gate (step 5)
- Never mark a task done without running its verification
- If a task requires HUMAN APPROVAL, stop and ask before proceeding past the approval point
- If implementation reveals a flaw in ARCHITECTURE.md, flag it — don't silently deviate
- If a task is blocked by a dependency, say so and suggest which task to do instead
