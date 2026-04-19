"""On-demand iperf3 bandwidth probe (paramiko-based SSH).

Flow per test cycle
───────────────────
1. SSH → ensure iperf3 is installed (auto-install via apt if missing)
2. SSH → kill any stale iperf3 -s process
3. SSH → start iperf3 -s in background
4. Local → iperf3 -c <host>    (upload:   local → remote)
5. Local → iperf3 -c <host> -R (download: remote → local)
6. SSH → kill iperf3 -s (cleanup, same connection)

Port 5201 is only open for ~(2 * duration + 5) seconds per cycle.
Auth: password if provided in host config, otherwise key/agent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time

import paramiko


def _make_client(
    user: str,
    host: str,
    port: int,
    password: str | None = None,
) -> paramiko.SSHClient:
    """Return a connected SSHClient.

    Uses password auth if *password* is given; otherwise falls back to
    the running ssh-agent and default key files (~/.ssh/id_rsa etc.).
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = dict(
        hostname=host,
        port=port,
        username=user,
        timeout=10,
        banner_timeout=15,
    )
    if password:
        kwargs.update(password=password, look_for_keys=False, allow_agent=False)
    # else: paramiko tries agent + ~/.ssh/id_rsa, id_ecdsa, id_ed25519 automatically
    client.connect(**kwargs)
    return client


def _exec(
    client: paramiko.SSHClient,
    cmd: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run *cmd* on an open SSH client; return (rc, stdout, stderr)."""
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    rc = stdout.channel.recv_exit_status()
    return (
        rc,
        stdout.read().decode(errors="replace"),
        stderr.read().decode(errors="replace"),
    )


def _run_client(
    host: str,
    port: int,
    duration: int,
    reverse: bool = False,
) -> float | None:
    """Run local iperf3 client; return measured Mbps or None on failure."""
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


def probe(host_config: dict, duration: int = 5, iperf3_port: int = 5201) -> dict:
    """Upload + download bandwidth test for *host_config*."""
    if not shutil.which("iperf3"):
        return {
            "success": False,
            "error": "iperf3 not installed locally — run: brew install iperf3",
        }

    host     = host_config["host"]
    user     = host_config["ssh_user"]
    port     = host_config.get("ssh_port", 22)
    password = host_config.get("password") or None

    try:
        client = _make_client(user, host, port, password)
    except Exception as exc:
        return {"success": False, "error": f"SSH connect failed: {exc}"}

    try:
        # 1. Ensure iperf3 is installed on the remote
        rc, _, _ = _exec(client, "command -v iperf3", timeout=10)
        if rc != 0:
            rc, _, err = _exec(
                client,
                "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
                "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q iperf3",
                timeout=120,
            )
            if rc != 0:
                return {"success": False, "error": f"auto-install failed: {err.strip()}"}

        # 2. Kill any stale server process
        _exec(client, "pkill -f 'iperf3 -s' 2>/dev/null || true")
        time.sleep(0.4)

        # 3. Start iperf3 server in background
        rc, _, err = _exec(
            client,
            f"nohup iperf3 -s -p {iperf3_port} >/dev/null 2>&1 & echo ok",
        )
        if rc != 0:
            return {"success": False, "error": f"failed to start iperf3 server: {err.strip()}"}
        time.sleep(1.5)

        # 4 & 5. Run local iperf3 client (upload then download)
        upload_mbps   = _run_client(host, iperf3_port, duration, reverse=False)
        download_mbps = _run_client(host, iperf3_port, duration, reverse=True)

        if upload_mbps is None and download_mbps is None:
            return {
                "success": False,
                "error": "iperf3 client failed — is iperf3 installed locally?",
            }

        return {
            "success":       True,
            "upload_mbps":   round(upload_mbps,   1) if upload_mbps   is not None else None,
            "download_mbps": round(download_mbps, 1) if download_mbps is not None else None,
        }

    except Exception as exc:
        return {"success": False, "error": str(exc)}

    finally:
        # 6. Always kill the remote server and close connection
        try:
            _exec(client, "pkill -f 'iperf3 -s' 2>/dev/null || true", timeout=8)
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
