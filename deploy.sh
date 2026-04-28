#!/usr/bin/env bash
# deploy.sh — Sync pinger to a remote host and (re)start the web UI.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh [-f] [user@host]      # e.g. darwin@ubuntu-1.local
#
# Options:
#   -f, --force   Force restart even if no server-affecting files changed
#
# Defaults to root@ubuntu-1.local if no argument is given.

set -euo pipefail

FORCE=0
ARGS=()
for arg in "$@"; do
    case "$arg" in
        -f|--force) FORCE=1 ;;
        *) ARGS+=("$arg") ;;
    esac
done

TARGET="${ARGS[0]:-darwin@ubuntu-1.local}"
REMOTE_DIR="./pinger"

# Stamp the build: prefer git short hash, fall back to timestamp
VERSION=$(python3 -c "from checksum import compute_repo_checksum; print(compute_repo_checksum()[:8])" 2>/dev/null || git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)
DEPLOYED_AT=$(date +"%Y-%m-%d %H:%M:%S %Z")
printf "==> Version (repo checksum): \033[1m%s\033[0m (deployed %s)\n" "${VERSION}" "${DEPLOYED_AT}"

echo "==> Syncing files to ${TARGET}:${REMOTE_DIR}"
SYNC_LOG=$(python3 checksum.py --files-only | rsync -av \
    --no-perms --no-owner --no-group \
    --files-from=- \
    . "${TARGET}:${REMOTE_DIR}/" 2>&1)
echo "$SYNC_LOG"

# Restart only when files that affect the running server changed:
#   *.py        — application/probe logic
#   *.html      — Jinja2 templates
#   requirements.txt — dependency changes require reinstall + restart
if [[ "$FORCE" -eq 0 ]] && ! echo "$SYNC_LOG" | grep -qE '\.(py|html)$|^requirements\.txt$'; then
    echo "==> No server-affecting files changed; skipping restart (use -f to force)"
    echo "==> Done. Web UI should be available at http://$(echo "$TARGET" | cut -d@ -f2):8080"
    exit 0
fi

echo "==> Restarting app on ${TARGET}"
ssh "${TARGET}" "PINGER_CHECKSUM='${VERSION}' bash" <<'REMOTE'
set -e
cd ./pinger

# Ensure a usable venv exists (recreate if missing or broken)
if [ ! -x venv/bin/python ]; then
    echo "  --> Creating virtual environment"
    rm -rf venv
    if python3 -m venv venv 2>/dev/null; then
        echo "  --> venv created"
    else
        echo "  --> venv creation failed; attempting to install python3-venv and retry"
        if [ "$(id -u)" -ne 0 ]; then SUDO='sudo'; else SUDO=''; fi
        $SUDO apt-get update -y
        $SUDO apt-get install -y python3-venv
        python3 -m venv venv
        echo "  --> venv created after installing python3-venv"
    fi
fi

# Ensure pip is available in venv; avoid hanging network bootstrap fallback
if ! venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "  --> pip missing in venv; trying ensurepip"
    venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1 || true
fi
if ! venv/bin/python -m pip --version >/dev/null 2>&1; then
    echo "  --> pip still unavailable in venv; please install python3-venv and rerun"
    exit 1
fi

echo "  --> Installing/updating dependencies in venv"
venv/bin/python -m pip install --upgrade pip setuptools >/dev/null
venv/bin/python -m pip install -q -r requirements.txt

echo "  --> Stopping any previous instance"
pkill -f "python app.py" || true

echo "  --> Starting app with venv python"
nohup venv/bin/python app.py --host 0.0.0.0 > pinger.log 2>&1 &
echo "  --> Started (PID $!), log: ~/pinger/pinger.log"
REMOTE

echo "==> Done. Web UI should be available at http://$(echo "$TARGET" | cut -d@ -f2):8080"
