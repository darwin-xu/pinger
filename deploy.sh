#!/usr/bin/env bash
# deploy.sh — Sync pinger to a remote host and (re)start the web UI.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh [user@host]          # e.g. root@ubuntu-1.local
#
# Defaults to root@ubuntu-1.local if no argument is given.

set -euo pipefail

TARGET="${1:-root@ubuntu-1.local}"
REMOTE_DIR="./pinger"

# Stamp the build: prefer git short hash, fall back to timestamp
VERSION=$(git rev-parse --short HEAD 2>/dev/null || date -u +%Y%m%dT%H%M%SZ)
DEPLOYED_AT=$(date -u +"%Y-%m-%d %H:%M:%S UTC")
echo "${VERSION} (deployed ${DEPLOYED_AT})" > version.txt
echo "==> Version: $(cat version.txt)"

echo "==> Syncing files to ${TARGET}:${REMOTE_DIR}"
rsync -av \
  --no-perms --no-owner --no-group \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.git' \
  --exclude '.pytest_cache' \
  --exclude 'pinger.db' \
  --exclude 'pinger.db-shm' \
  --exclude 'pinger.db-wal' \
  . "${TARGET}:${REMOTE_DIR}/"

echo "==> Restarting app on ${TARGET}"
ssh "${TARGET}" bash <<'REMOTE'
set -e
cd ./pinger

# Create venv if it doesn't exist yet
if [ ! -f venv/bin/activate ]; then
  echo "  --> Creating virtual environment"
  python3 -m venv venv
  python3 -m ensurepip --upgrade 2>/dev/null || true
fi

source venv/bin/activate

echo "  --> Installing/updating dependencies"
python3 -m pip install -q -r requirements.txt

echo "  --> Stopping any previous instance"
pkill -f "python app.py" || true

echo "  --> Starting app"
nohup python3 app.py --host 0.0.0.0 > pinger.log 2>&1 &
echo "  --> Started (PID $!), log: ~/pinger/pinger.log"
REMOTE

echo "==> Done. Web UI should be available at http://$(echo "$TARGET" | cut -d@ -f2):8080"
