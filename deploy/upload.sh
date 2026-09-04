#!/usr/bin/env bash
# Upload this project to a Hostinger VPS from your Mac.
# Usage:
#   ./deploy/upload.sh root@YOUR_VPS_IP
# Optional second arg is the remote folder (default /var/www/reviewhub).
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: ./deploy/upload.sh root@YOUR_VPS_IP [remote_dir]"
  exit 1
fi

HOST="$1"
REMOTE="${2:-/var/www/reviewhub}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Uploading ${ROOT} -> ${HOST}:${REMOTE}"
ssh "$HOST" "mkdir -p '${REMOTE}'"
rsync -avz --delete \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude '.DS_Store' \
  --exclude '.cursor' \
  --exclude 'static/uploads' \
  "${ROOT}/" "${HOST}:${REMOTE}/"

echo
echo "Upload finished. On the VPS run:"
echo "  ssh ${HOST}"
echo "  bash ${REMOTE}/deploy/setup-vps.sh     # first time only"
echo "  nano ${REMOTE}/.env                    # add keys, then:"
echo "  systemctl restart reviewhub"
