#!/usr/bin/env bash
# deploy_iperf3.sh — Install iperf3 on one or more Ubuntu/Debian VPS servers.
#
# Usage:
#   chmod +x deploy_iperf3.sh
#   ./deploy_iperf3.sh user@host1 [user@host2 ...]
#
# Requirements (local):
#   - SSH access with key-based auth (or ssh-agent loaded)
#   - sudo rights on each remote host
#
# After this script, iperf3 is installed but NOT running as a service.
# The pinger tool starts/stops it on-demand during each bandwidth probe.

set -euo pipefail

REMOTE_SCRIPT='
set -e
if command -v iperf3 >/dev/null 2>&1; then
    echo "Already installed: $(iperf3 --version 2>&1 | head -1)"
else
    echo "Installing iperf3..."
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q iperf3
    echo "Installed: $(iperf3 --version 2>&1 | head -1)"
fi
echo "OK"
'

if [[ $# -eq 0 ]]; then
    echo "Usage: $0 user@host1 [user@host2 ...]"
    echo "Example: $0 root@192.168.1.1 ubuntu@vps2.example.com"
    exit 1
fi

SUCCESS=0
FAIL=0

for TARGET in "$@"; do
    echo "──────────────────────────────────────────────"
    echo "  Target: ${TARGET}"
    echo "──────────────────────────────────────────────"

    if ssh \
        -o ConnectTimeout=10 \
        -o StrictHostKeyChecking=no \
        -o BatchMode=yes \
        -o LogLevel=ERROR \
        "${TARGET}" \
        "${REMOTE_SCRIPT}"; then
        echo "✓ Done: ${TARGET}"
        (( SUCCESS += 1 )) || true
    else
        echo "✗ Failed: ${TARGET}"
        (( FAIL += 1 )) || true
    fi
    echo
done

echo "══════════════════════════════════════════════"
echo "  Results: ${SUCCESS} succeeded, ${FAIL} failed"
echo "══════════════════════════════════════════════"
echo
echo "iperf3 is installed but NOT running as a service."
echo "Port 5201 stays closed; pinger opens it briefly during each probe."
