---
description: "Run this checklist before merging a refactor: tests, docs, thread-safety, and CI-safe verifications."
name: "safe-refactor-checklist"
argument-hint: "target file(s) or 'all'"
agent: "agent"
---

Run a compact, reproducible checklist to validate a refactor. Supply a file path or `all`.

Checklist (actionable steps):

1. Run unit tests locally (fast path):

```bash
# run all unit tests (skips integration marker if configured)
pytest -q -m "not integration"

# run tests for a single file
pytest -q path/to/test_file.py
```

2. Run the formatting/quality check for changed files:

```bash
# run the repository's tests for formatting helper
pytest test_formatting.py
```

3. Verify no new network-dependent unit tests were added (unit tests must not perform SSH/ping/iperf3):

- If tests reference network calls, mark them with `@pytest.mark.integration` and move to integration suite.

4. Confirm thread-safety and live-state access:

- If changes read from shared runtime state, ensure `ProbeEngine.snapshot()` is used for reads and that mutations hold locks.

5. Smoke-run the relevant entrypoint(s):

```bash
# For web changes: start the web UI and ensure it boots
.venv/bin/python app.py --port 8080 &
# For CLI changes: run main.py briefly
.venv/bin/python main.py --config config.yaml
```

6. Update docs and guidance if behavior changed:

- Update `README.md` or `AGENTS.md` if public behavior, config, or run commands changed.

Output format requested from the agent:
- Short status: `OK` or `FAIL` per step
- Command snippets and failing test names (if any)
- Suggested next steps (one-liner fixes)

Example invocations:
- `/safe-refactor-checklist src/formatting.py`
- `/safe-refactor-checklist all`

Suggested follow-ups:
- Create a small `scripts/check_refactor.sh` to automate steps 1–3.
- Add a CI job that runs this checklist on PRs.
