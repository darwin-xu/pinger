---
name: triage-probe-failures
description: "Interactive triage workflow for probe failures (ping / tcp / iperf3). Use to gather evidence, run deterministic checks, and propose likely root causes and fixes."
argument-hint: "host name or 'all'"
user-invocable: true
disable-model-invocation: false
---

# Triage Probe Failures

## When to use
- Run this skill when one or more probes show `success: false`, degraded metrics, or when the dashboard shows repeated warnings or red statuses.

## Goal
- Produce a concise diagnosis (most likely cause), evidence (metrics and recent history), and actionable next steps to verify and remediate.

## Quick checklist
1. Collect the latest snapshot: `engine.snapshot()` (results + history).
2. Pull recent persisted rows for the target host from `storage.recent(host, probe)` and `storage.latest(host, probe)`.
3. Correlate failures across probes (ping vs tcp vs iperf3) and across hosts.
4. Classify probable cause and provide commands to verify.

## Procedure (step-by-step)

1) Identify scope
- Argument: `host` (display name) or `all`. If `all`, focus on cross-host correlation.

2) Gather immediate evidence
- Engine snapshot (in-memory): call `ProbeEngine.snapshot()` to get current `results` and `history`.
- Persisted history: use `storage.recent(host_ip, probe, limit=50)` for `ping`, `tcp`, `iperf3`.

3) Heuristics to classify failures
- Ping fails, TCP ok → ICMP blocked by firewall (safe to mark ICMP filtered).
- TCP fails (connection refused / timeout) → SSH port closed, host down, or routing issue.
- Both ping and tcp fail → host unreachable or major network outage.
- iperf3 errors mentioning "not installed" → remote iperf3 install problem or sudo missing.
- High ping latency across many hosts → local network or upstream ISP congestion.
- High latency for single host only → remote VM load, route issue, or upstream hop.

4) Suggested verification commands (local)

```bash
# Quick engine snapshot via Python REPL
python - <<'PY'
from engine import load_config, ProbeEngine
cfg = load_config('config.yaml')
e = ProbeEngine(cfg)
e.start()
snap_r, snap_h = e.snapshot()
print(snap_r)
e.stop()
PY

# Inspect DB (recent ping rows for host_ip)
sqlite3 pinger.db "SELECT ts, data FROM metrics WHERE host='1.2.3.4' AND probe='ping' ORDER BY ts DESC LIMIT 20;"
```

5) Remediation hints
- ICMP filtered: rely on TCP probe (SSH) for availability checks; document firewall rules needed.
- TCP down: verify SSH keys, firewall, and remote service status; ask for `ssh -vvv user@host` output.
- iperf3 fail: attempt manual SSH and run `iperf3 --version` on remote; if auto-install failing, run `sudo apt update && sudo apt install -y iperf3`.

6) Produce final report
- Short diagnosis (one-line), supporting evidence (key metric rows or patterns), confidence (low/medium/high), and next steps (commands or manual checks).

## Examples (prompts to run)
- `/triage-probe-failures tokyo-1` — diagnose a single host.
- `/triage-probe-failures all` — find correlated network issues across hosts.

## References
- [engine.py](../../../engine.py)
- [storage.py](../../../storage.py)
- [probes/ping.py](../../../probes/ping.py)
- [probes/tcp.py](../../../probes/tcp.py)
- [probes/iperf3.py](../../../probes/iperf3.py)
