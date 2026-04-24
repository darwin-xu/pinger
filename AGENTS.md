# AGENTS Instructions for pinger

This file helps AI coding agents work effectively in this repository.

## Start Here

- Read [README.md](README.md) for architecture, behavior, and operational notes.
- Use [config.yaml.example](config.yaml.example) as the configuration schema reference.
- Core runtime logic is in [engine.py](engine.py) and web endpoints are in [app.py](app.py).
- Probe implementations are in [probes/ping.py](probes/ping.py), [probes/tcp.py](probes/tcp.py), and [probes/iperf3.py](probes/iperf3.py).

## Environment and Commands

- Python: 3.11+
- Setup:
  - python3 -m venv .venv
  - source .venv/bin/activate
  - pip install -r requirements.txt
- Run web UI: .venv/bin/python app.py
- Run CLI mode: .venv/bin/python main.py --config config.yaml
- Run tests: pytest test_formatting.py

## Architecture and Boundaries

- [app.py](app.py): Flask UI + JSON APIs, host/settings CRUD, manual iperf3 trigger.
- [engine.py](engine.py): Shared scheduler and background workers.
- [storage.py](storage.py): SQLite persistence and history retrieval.
- [display.py](display.py): Rich terminal rendering for CLI mode only.
- [formatting.py](formatting.py): Shared duration/number formatting logic.

## Repository Conventions

- Keep changes minimal and scoped to the request.
- Always update [README.md](README.md) when behavior, commands, config, or features change.
- Preserve configuration compatibility with [config.yaml.example](config.yaml.example).

## Pitfalls and Guardrails

- Thread safety: use ProbeEngine.snapshot() for reads rather than accessing live mutable state.
- Host identity: persistence is keyed by host IP in storage; UI displays host name.
- iperf3 behavior: server is started on demand and stopped after each run.
- Optional host passwords may exist in config; avoid logging or exposing secrets.

## Validation Checklist for Code Changes

- Run relevant tests (at least pytest test_formatting.py when touching formatting).
- If runtime behavior changed, run the affected entry point locally:
  - Web flow: app.py + dashboard/API sanity check.
  - CLI flow: main.py rendering and clean Ctrl-C shutdown.
- If user-facing behavior changed, update [README.md](README.md).
