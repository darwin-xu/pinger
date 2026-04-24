---
description: "Use when adding or modifying Python tests (pytest). Includes run commands, naming conventions, and guidance for avoiding network-dependent unit tests."
applyTo:
  - "**/test_*.py"
  - "**/*_test.py"
name: "testing-python-files"
---

# Testing Python Files — Project Guidance

- **Run tests (local):**

```bash
# run all tests
pytest

# run a single test file
pytest test_formatting.py
```

- **Naming & structure:**
  - Test files should use the `test_*.py` or `*_test.py` pattern.
  - Test functions and methods should be named `test_*` and keep one assertion per logical expectation when practical.

- **Unit vs Integration:**
  - Unit tests MUST NOT perform network I/O (no real SSH, ping, or iperf3 calls).
  - Put network-dependent cases behind an `integration` pytest marker and run them separately:

```bash
# run only integration tests
pytest -m integration
```

- **Mocking guidelines:**
  - For probes, mock external calls (e.g., socket, subprocess, paramiko) and return representative probe dicts.
  - Use `monkeypatch` or `unittest.mock` fixtures consistently.

- **Flakiness & determinism:**
  - Avoid sleeps or timing-based assertions; prefer deterministic inputs and explicit timestamps.
  - Use fixed random seeds where randomness is involved.

- **When changing formatting helpers (`formatting.py`):**
  - Update `test_formatting.py` accordingly and run `pytest test_formatting.py` before opening a PR.

- **CI / Review checklist:**
  - All unit tests pass locally with `pytest`.
  - Integration tests are documented and gated behind `-m integration` or CI jobs.
  - If a change modifies public behavior, update [README.md](README.md) or `AGENTS.md` as appropriate.
