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
) -> tuple[int, str]:
    """Run *cmd* on the SSH client; return (rc, combined_output).

    stderr is merged into stdout at the shell level so that:
    * All output (error messages, progress) is captured in one string.
    * There is no pipe-buffer deadlock — we drain a single stream before
      calling recv_exit_status(), so the remote process can always write.
    """
    _, stdout, _ = client.exec_command(f"( {cmd} ) 2>&1", timeout=timeout)
    out = stdout.read().decode(errors="replace").strip()
    rc = stdout.channel.recv_exit_status()
    return rc, out


def _run_client(
    host: str,
    port: int,
    duration: int,
    reverse: bool = False,
) -> tuple[float | None, str | None]:
    """Run local iperf3 client; return (Mbps, error_string).

    On success: (float, None).
    On failure: (None, human-readable reason).
    iperf3 -J outputs a JSON {"error": "..."} on failure, so we parse that
    to get the real reason (e.g. "Connection timed out", "Connection refused").
    """
    cmd = ["iperf3", "-c", host, "-p", str(port), "-t", str(duration), "-J"]
    if reverse:
        cmd.append("-R")
    direction = "download" if reverse else "upload"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=duration + 30)
        # iperf3 always emits JSON with -J, even on error
        try:
            data = json.loads(r.stdout)
        except (json.JSONDecodeError, ValueError):
            # No JSON at all — binary probably not found or crashed immediately
            msg = r.stderr.strip() or r.stdout.strip() or "iperf3 produced no output"
            return None, f"{direction}: {msg}"
        if r.returncode != 0:
            err = data.get("error") or r.stderr.strip() or "unknown error"
            return None, f"{direction}: {err}"
        bps_key = "sum_received" if reverse else "sum_sent"
        return data["end"][bps_key]["bits_per_second"] / 1e6, None
    except subprocess.TimeoutExpired:
        return None, f"{direction}: timed out after {duration + 30}s (firewall?)"
    except FileNotFoundError:
        return None, "iperf3 binary not found locally — install with: brew install iperf3"
    except Exception as exc:
        return None, f"{direction}: {exc}"


def _install_iperf3(client: paramiko.SSHClient) -> tuple[int, str]:
    """Try to install iperf3 using whatever package manager is available.

    Returns (rc, combined_output) — rc==0 means success.
    Tries apt-get, then dnf, then yum, then apk.
    """
    # Detect package manager
    for pm, install_cmd in [
        ("apt-get",
         "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
         "DEBIAN_FRONTEND=noninteractive apt-get install -y -q iperf3"),
        ("dnf",  "dnf install -y -q iperf3"),
        ("yum",  "yum install -y -q iperf3"),
        ("apk",  "apk add --no-cache iperf3"),
    ]:
        rc_which, _ = _exec(client, f"command -v {pm}", timeout=10)
        if rc_which == 0:
            return _exec(client, f"sudo {install_cmd}", timeout=180)
    return 1, "no supported package manager found (tried apt-get, dnf, yum, apk)"


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
        rc, _ = _exec(client, "command -v iperf3", timeout=10)
        if rc != 0:
            rc, out = _install_iperf3(client)
            if rc != 0:
                return {"success": False, "error": f"auto-install failed: {out or '(no output)'}"}

        # 2. Kill any stale server process
        _exec(client, "pkill -f 'iperf3 -s' 2>/dev/null || true")
        time.sleep(0.4)

        # 3. Start iperf3 server in background
        rc, out = _exec(
            client,
            f"nohup iperf3 -s -p {iperf3_port} >/dev/null 2>&1 & echo ok",
        )
        if rc != 0:
            return {"success": False, "error": f"failed to start iperf3 server: {out}"}
        time.sleep(1.5)

        # 4 & 5. Run local iperf3 client (upload then download)
        upload_mbps,   ul_err = _run_client(host, iperf3_port, duration, reverse=False)
        download_mbps, dl_err = _run_client(host, iperf3_port, duration, reverse=True)

        if upload_mbps is None and download_mbps is None:
            errors = "; ".join(filter(None, [ul_err, dl_err]))
            return {
                "success": False,
                "error": errors or "iperf3 client failed",
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
