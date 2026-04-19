"""On-demand iperf3 bandwidth probe.

Flow per test cycle
───────────────────
1. SSH → kill any stale iperf3 -s on the remote
2. SSH → start iperf3 -s in background (daemonised with nohup)
3. Local → iperf3 -c <host>    (upload:   local → remote)
4. Local → iperf3 -c <host> -R (download: remote → local)
5. SSH → kill iperf3 -s (cleanup)

Port 5201 is only open for ~(2 * duration + 5) seconds per cycle.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time


def _ssh(
    user: str,
    host: str,
    port: int,
    key: str | None,
    cmd: str,
    timeout: int = 15,
) -> subprocess.CompletedProcess:
    """Run *cmd* on the remote host via SSH."""
    args = [
        "ssh",
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=8",
        "-o", "BatchMode=yes",
        "-o", "LogLevel=ERROR",
        "-p", str(port),
    ]
    if key:
        args += ["-i", os.path.expanduser(key)]
    args += [f"{user}@{host}", cmd]
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _run_client(
    host: str,
    port: int,
    duration: int,
    reverse: bool = False,
) -> float | None:
    """Run iperf3 client; return measured Mbps or None on failure."""
    cmd = ["iperf3", "-c", host, "-p", str(port), "-t", str(duration), "-J"]
    if reverse:
        cmd.append("-R")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 25)
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout)
        bps_key = "sum_received" if reverse else "sum_sent"
        return data["end"][bps_key]["bits_per_second"] / 1e6
    except Exception:
        return None


def _ensure_remote_iperf3(
    user: str,
    host: str,
    port: int,
    key: str | None,
) -> tuple[bool, str]:
    """Check if iperf3 is installed on the remote; install via apt if not.

    Returns (ok, message).
    """
    r = _ssh(user, host, port, key, "command -v iperf3", timeout=10)
    if r.returncode == 0:
        return True, "already installed"

    # Not found – try to install
    install_cmd = (
        "sudo DEBIAN_FRONTEND=noninteractive "
        "apt-get update -qq && "
        "sudo DEBIAN_FRONTEND=noninteractive "
        "apt-get install -y -q iperf3"
    )
    r = _ssh(user, host, port, key, install_cmd, timeout=120)
    if r.returncode != 0:
        return False, f"auto-install failed: {r.stderr.strip()}"

    # Verify
    r = _ssh(user, host, port, key, "command -v iperf3", timeout=10)
    if r.returncode == 0:
        return True, "just installed"
    return False, "install succeeded but iperf3 not in PATH"


def probe(host_config: dict, duration: int = 5, iperf3_port: int = 5201) -> dict:
    """Upload + download bandwidth test for *host_config*."""
    if not shutil.which("iperf3"):
        return {"success": False, "error": "iperf3 not installed locally — run: brew install iperf3"}

    host = host_config["host"]
    user = host_config["ssh_user"]
    port = host_config.get("ssh_port", 22)
    key  = host_config.get("ssh_key")

    try:
        # 0. Ensure iperf3 is installed on the remote
        ok, msg = _ensure_remote_iperf3(user, host, port, key)
        if not ok:
            return {"success": False, "error": f"remote iperf3: {msg}"}

        # 1. Clean up any leftover server process
        _ssh(user, host, port, key, "pkill -f 'iperf3 -s' 2>/dev/null || true")
        time.sleep(0.4)

        # 2. Start iperf3 server in background
        r = _ssh(
            user, host, port, key,
            f"nohup iperf3 -s -p {iperf3_port} >/dev/null 2>&1 & echo ok",
        )
        if r.returncode != 0:
            return {"success": False, "error": f"SSH failed: {r.stderr.strip()}"}
        time.sleep(1.5)   # let the server bind its port

        # 3 & 4. Upload then download
        upload_mbps   = _run_client(host, iperf3_port, duration, reverse=False)
        download_mbps = _run_client(host, iperf3_port, duration, reverse=True)

        if upload_mbps is None and download_mbps is None:
            return {"success": False, "error": "iperf3 client failed — is iperf3 installed on the VPS?"}

        return {
            "success":       True,
            "upload_mbps":   round(upload_mbps,   1) if upload_mbps   is not None else None,
            "download_mbps": round(download_mbps, 1) if download_mbps is not None else None,
        }

    except Exception as exc:
        return {"success": False, "error": str(exc)}

    finally:
        # 5. Always kill the remote server
        try:
            _ssh(user, host, port, key, "pkill -f 'iperf3 -s' 2>/dev/null || true", timeout=8)
        except Exception:
            pass
