"""TCP connect-time probe.

Measures the round-trip time for a full TCP three-way handshake.
No server software is required beyond an open listening port.
Defaults to SSH (port 22) which is present on every managed VPS.
"""
from __future__ import annotations

import socket
import time


def probe(host: str, port: int = 22, timeout: float = 5.0) -> dict:
    """Return the TCP handshake RTT in milliseconds."""
    try:
        start = time.perf_counter()
        with socket.create_connection((host, port), timeout=timeout):
            rtt_ms = (time.perf_counter() - start) * 1000
        return {"success": True, "rtt": round(rtt_ms, 2)}
    except OSError as exc:
        return {"success": False, "error": str(exc)}
