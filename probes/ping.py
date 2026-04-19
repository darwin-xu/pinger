"""ICMP ping probe via the system `ping` binary.

Returns a dict with keys:
  success (bool), avg (ms), min (ms), max (ms), jitter (ms), loss (%), p95 (ms)
"""
from __future__ import annotations

import platform
import re
import subprocess


def probe(host: str, count: int = 10, interval: float = 0.2) -> dict:
    """Ping *host* and return latency statistics."""
    system = platform.system()
    if system == "Darwin":
        cmd = ["ping", "-c", str(count), "-i", str(interval), host]
    else:
        # Linux: -W sets per-packet timeout in seconds
        cmd = ["ping", "-c", str(count), "-i", str(interval), "-W", "2", host]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=count * (interval + 1.5) + 5,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "ping timed out"}
    except FileNotFoundError:
        return {"success": False, "error": "ping binary not found"}

    output = result.stdout + result.stderr

    # Individual RTT lines for p95 calculation
    rtt_hits = re.findall(r"time=([\d.]+)\s*ms", output)
    rtts = sorted(float(x) for x in rtt_hits)
    p95: float | None = None
    if len(rtts) >= 2:
        p95 = rtts[int(len(rtts) * 0.95)]
    elif len(rtts) == 1:
        p95 = rtts[0]

    # Packet loss  (handles "0%" and "0.0%")
    loss_match = re.search(r"([\d.]+)%\s+packet loss", output)
    loss = float(loss_match.group(1)) if loss_match else 100.0

    # Summary line: works for both macOS and Linux
    #   macOS:  round-trip min/avg/max/stddev = X/Y/Z/W ms
    #   Linux:  rtt min/avg/max/mdev = X/Y/Z/W ms
    rtt_match = re.search(
        r"min/avg/max(?:/(?:mdev|stddev))?\s*=\s*"
        r"([\d.]+)/([\d.]+)/([\d.]+)(?:/([\d.]+))?",
        output,
    )
    if rtt_match:
        return {
            "success": True,
            "min":    float(rtt_match.group(1)),
            "avg":    float(rtt_match.group(2)),
            "max":    float(rtt_match.group(3)),
            "jitter": float(rtt_match.group(4)) if rtt_match.group(4) else 0.0,
            "loss":   loss,
            "p95":    p95,
        }

    # All packets lost or unparseable
    return {"success": False, "error": "no reply", "loss": loss, "p95": None}
