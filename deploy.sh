#!/usr/bin/env bash
# deploy.sh — Sync pinger to a remote host and (re)start the web UI.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh [-f] [-t 1|2] [user@host]
#
# Options:
#   -f, --force        Force restart even if no server-affecting files changed
#   -t, --target N     Deployment target: 1 = darwin@ubuntu-1.local (old, default)
#                                         2 = root@ubuntu-a.local (new)
#
# An explicit user@host argument overrides -t.

set -euo pipefail

FORCE=0
TARGET_ID=1
ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f|--force) FORCE=1 ;;
        -t|--target)
            if [[ $# -lt 2 ]]; then
                echo "Error: $1 requires a value (1 or 2)" >&2
                exit 1
            fi
            TARGET_ID="$2"
            shift
            ;;
        *) ARGS+=("$1") ;;
    esac
    shift
done

case "$TARGET_ID" in
    1) DEFAULT_TARGET="darwin@ubuntu-1.local" ;;
    2) DEFAULT_TARGET="root@ubuntu-a.local" ;;
    *)
        echo "Error: invalid target '${TARGET_ID}' (use 1 for ubuntu-1.local or 2 for ubuntu-a.local)" >&2
        exit 1
        ;;
esac

TARGET="${ARGS[0]:-$DEFAULT_TARGET}"
REMOTE_DIR="./pinger"

# Reuse one SSH connection for every step so a password-only host prompts once
SSH_OPTS=(-o ControlMaster=auto -o "ControlPath=/tmp/pinger-ssh-%C" -o ControlPersist=300)

# Stamp the build: prefer git short hash, fall back to timestamp
VERSION=$(python3 -c "from checksum import compute_repo_checksum; print(compute_repo_checksum()[:8])" 2>/dev/null || git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)
DEPLOYED_AT=$(date +"%Y-%m-%d %H:%M:%S %Z")
printf "==> Version (repo checksum): \033[1m%s\033[0m (deployed %s)\n" "${VERSION}" "${DEPLOYED_AT}"

echo "==> Preparing ${TARGET} (installing rsync, python3, python3-venv if missing)"
ssh "${SSH_OPTS[@]}" "${TARGET}" bash <<'BOOTSTRAP'
set -e
mkdir -p ~/pinger
NEED_APT=0
command -v rsync >/dev/null 2>&1 || NEED_APT=1
command -v python3 >/dev/null 2>&1 || NEED_APT=1
python3 -c "import ensurepip" >/dev/null 2>&1 || NEED_APT=1
if [ "$NEED_APT" -eq 1 ]; then
    echo "  --> Installing rsync, python3, python3-venv"
    if [ "$(id -u)" -ne 0 ]; then SUDO='sudo'; else SUDO=''; fi
    $SUDO apt-get update -y </dev/null
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y rsync python3 python3-venv </dev/null
fi
echo "  --> Host ready"
BOOTSTRAP

# A host without a venv has never been built, so restart even if nothing changed
if ! ssh "${SSH_OPTS[@]}" "${TARGET}" 'test -x ~/pinger/venv/bin/python'; then
    echo "==> No virtual environment on ${TARGET}; forcing first-time build"
    FORCE=1
fi

echo "==> Syncing files to ${TARGET}:${REMOTE_DIR}"
if ! SYNC_LOG=$(python3 checksum.py --files-only | rsync -av \
    --no-perms --no-owner --no-group \
    -e "ssh ${SSH_OPTS[*]}" \
    --files-from=- \
    . "${TARGET}:${REMOTE_DIR}/" 2>&1); then
    echo "$SYNC_LOG" >&2
    echo "==> Sync to ${TARGET} failed" >&2
    exit 1
fi
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
ssh "${SSH_OPTS[@]}" "${TARGET}" "PINGER_CHECKSUM='${VERSION}' bash" <<'REMOTE'
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
nohup venv/bin/python app.py --host 0.0.0.0 > pinger.log 2>&1 < /dev/null &
echo "  --> Started (PID $!), log: ~/pinger/pinger.log"
REMOTE

echo "==> Done. Web UI should be available at http://$(echo "$TARGET" | cut -d@ -f2):8080"
