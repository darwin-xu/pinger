# pinger — VPS Network Stability Monitor

A tool that periodically probes multiple VPS servers with three complementary measurement methods and displays the results in a live, color-coded **web dashboard**. Configuration (hosts, intervals, thresholds) is managed from the browser — no need to hand-edit YAML.

---

## Design

### Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Your Machine                                                │
│                                                              │
│  app.py (Flask)                                              │
│  ├── Web dashboard  →  http://localhost:8080                 │
│  │    ├── Live probe results (auto-refresh every 5 s)       │
│  │    ├── Add / edit / remove VPS hosts                     │
│  │    └── Adjust intervals & thresholds                     │
│  │                                                          │
│  └── engine.py (background threads, auto-started)           │
│       ├── Ping probe   (every N s, parallel)                │
│       │     └── ICMP via system ping → latency, jitter, loss│
│       ├── TCP probe    (every N s, parallel)                │
│       │     └── TCP connect to SSH port → handshake RTT     │
│       └── iperf3 probe (every M s, sequential)              │
│             ├── SSH → auto-install iperf3 if missing        │
│             ├── SSH → start iperf3 -s briefly               │
│             ├── iperf3 client: upload + download test       │
│             └── SSH → kill iperf3 -s (port closed again)    │
└──────────────────────────────────────────────────────────────┘
```

### Key design decisions

- **On-demand iperf3**: iperf3 only runs during the test window (~10–15 s) and is killed immediately after. Port 5201 stays closed the rest of the time.
- **Auto-install**: The iperf3 probe detects whether `iperf3` is installed on each VPS and installs it via `apt` automatically if missing. No manual `deploy_iperf3.sh` step required.
- **Web config**: Hosts and settings live in `config.yaml` but are edited via the web UI. Changes apply immediately (hot-reload).

### File layout

```
pinger/
├── app.py               # Flask web server + API (main entry point)
├── engine.py             # Shared probe scheduler (background threads)
├── main.py               # Optional CLI mode (Rich terminal dashboard)
├── display.py            # Rich terminal table builder (used by main.py)
├── storage.py            # SQLite time-series store
├── config.yaml           # Auto-managed by web UI (or hand-edit)
├── probes/
│   ├── ping.py           # ICMP probe (system ping binary)
│   ├── tcp.py            # TCP connect-time probe (SSH port)
│   └── iperf3.py         # On-demand SSH + auto-install + iperf3
├── templates/
│   └── index.html        # Web dashboard
├── deploy_iperf3.sh      # Optional: bulk-install iperf3 on many VPS
└── requirements.txt
```

### Metrics collected

| Probe  | Metrics |
|--------|---------|
| Ping   | avg / min / max latency (ms), jitter (ms), packet loss (%), p95 RTT |
| TCP    | TCP handshake RTT to SSH port (ms) |
| iperf3 | Download Mbps, Upload Mbps |

All results are persisted to `pinger.db` (SQLite) so history survives restarts.

### Dashboard

Color coding:

| Color  | Meaning |
|--------|---------|
| Green  | Within normal thresholds |
| Yellow | Degraded (warn threshold exceeded) |
| Red    | Critical (crit threshold exceeded) |
| `✓`    | Healthy |
| `⚠`    | Warning (loss ≥ 1 % or TCP failed) |
| `✗`    | Unreachable (loss ≥ 50 % or no reply) |

---

## Requirements

**Local machine:**
- Python 3.11+
- `iperf3` binary — `brew install iperf3` (macOS) or `sudo apt install iperf3` (Linux)
- SSH key-based access to all VPS targets

**Each VPS:**
- Ubuntu / Debian
- SSH access open
- `iperf3` is **auto-installed** on first bandwidth probe (requires sudo)

---

## Quick Start

### 1. Install Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start the web UI

```bash
.venv/bin/python app.py
# or bind to a custom port:
.venv/bin/python app.py --port 9090
```

Background probes start automatically. Open **http://localhost:8080** in your browser.

### 3. Add VPS targets

Use the **Add Host** form on the web page:

| Field     | Example           | Notes |
|-----------|-------------------|-------|
| Name      | `Tokyo-1`         | Display label |
| Host / IP | `1.2.3.4`         | |
| SSH User  | `root`            | |
| SSH Port  | `22`              | |
| SSH Key   | `~/.ssh/id_rsa`   | Optional; omit to use ssh-agent |
| iperf3    | ☑                 | Uncheck to skip bandwidth tests |

### 4. Adjust settings (optional)

Scroll down to the **Settings** section on the web page to change probe intervals and color thresholds. Changes are saved to `config.yaml` and take effect on the next probe cycle.

---

## CLI Mode (optional)

For a terminal-only Rich dashboard (no web server):

```bash
.venv/bin/python main.py [--config config.yaml]
```

Press `Ctrl-C` to quit.

---

## Configuration Reference

`config.yaml` is managed by the web UI, but can also be edited by hand:

```yaml
probe_interval:   30    # seconds between ping + TCP cycles
iperf3_interval:  300   # seconds between iperf3 cycles
ping_count:       10    # ICMP packets per probe run
iperf3_duration:  5     # seconds per iperf3 direction
iperf3_port:      5201  # port used during iperf3 tests

thresholds:
  ping_warn_ms:   100
  ping_crit_ms:   300
  loss_warn_pct:  1
  loss_crit_pct:  5
  tcp_warn_ms:    150
  tcp_crit_ms:    500
  bw_warn_mbps:   50
  bw_crit_mbps:   10

hosts:
  - name:     "Tokyo-1"
    host:     "1.2.3.4"
    ssh_user: "root"
    ssh_port: 22
    ssh_key:  "~/.ssh/id_rsa"
    iperf3:   true
```

### Tuning tips

- Increase `probe_interval` (e.g. `60`) to reduce noise and network traffic.
- Set `iperf3: false` for VPS where you only want latency monitoring.
- Lower `bw_warn_mbps` / `bw_crit_mbps` for servers with limited bandwidth.
- The TCP probe connects to `ssh_port`, doubling as an SSH availability check.

---

## Bulk iperf3 deploy (optional)

If you prefer to pre-install iperf3 on all VPS at once rather than waiting for auto-install:

```bash
chmod +x deploy_iperf3.sh
./deploy_iperf3.sh root@vps1.example.com ubuntu@vps2.example.com
```

---

## Data Storage

Results are stored in `pinger.db` (SQLite, created automatically):

```sql
metrics(id, ts TEXT, host TEXT, probe TEXT, data TEXT)
```

Query history directly:

```bash
sqlite3 pinger.db "SELECT ts, data FROM metrics WHERE host='Tokyo-1' AND probe='ping' ORDER BY ts DESC LIMIT 10;"
```

The history charts use `probe_interval` to identify missing probe windows. When adjacent samples are separated by more than two probe intervals, the connecting segment is drawn thinner and lighter so downtime or dashboard gaps are not mistaken for normal measurements. The TCP RTT failures legend shows the number of failed TCP probes in the current chart window and toggles the failure markers.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Ping shows `✗` but server is up | ICMP blocked by firewall | Check VPS firewall; TCP probe still works |
| iperf3 error: "not installed locally" | iperf3 missing on your Mac | `brew install iperf3` |
| iperf3 error: "auto-install failed" | sudo not available on VPS | SSH in and run `sudo apt install iperf3` manually |
| iperf3 error: "SSH failed" | Key auth not set up | Ensure SSH key works: `ssh user@host` |
| Dashboard not updating | Probe taking too long | Increase `probe_interval`; reduce `ping_count` |
