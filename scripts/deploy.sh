#!/usr/bin/env bash
# Sync the repository to the dev server, install the backend and (re)start the API in tmux.
#
# Configure in .env.deploy (git-ignored), see .env.example:
#   DEPLOY_HOST=root@1.2.3.4  DEPLOY_PORT=22  DEPLOY_KEY=~/.ssh/key  DEPLOY_DIR=/workspace/app
# Usage: scripts/deploy.sh [--no-restart]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.deploy ] && . ./.env.deploy
: "${DEPLOY_HOST:?set DEPLOY_HOST in .env.deploy}"
PORT="${DEPLOY_PORT:-22}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}"
DIR="${DEPLOY_DIR:-/workspace/app}"
VENV="${DEPLOY_VENV:-/workspace/venv}"
CACHE="${DEPLOY_CACHE:-/workspace/cache}"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -p "$PORT")

rsync -az --delete -e "ssh -i $KEY -o IdentitiesOnly=yes -p $PORT" \
  --exclude .git --exclude node_modules --exclude .venv --exclude __pycache__ \
  --exclude '.env*' --exclude '.DS_Store' ./ "$DEPLOY_HOST:$DIR/"

"${SSH[@]}" "$DEPLOY_HOST" "set -e; . $VENV/bin/activate; uv pip install -q -e '$DIR/backend[dev]'"

if [ "${1:-}" != "--no-restart" ]; then
  "${SSH[@]}" "$DEPLOY_HOST" "tmux kill-session -t api 2>/dev/null || true; \
    tmux new-session -d -s api 'cd $DIR && . $VENV/bin/activate && SPHEREX_CACHE_DIR=$CACHE SPHEREX_LOG_LEVEL=${DEPLOY_LOG_LEVEL:-INFO} \
    uvicorn spherex_api.main:app --host 0.0.0.0 --port 8000 --no-access-log 2>&1 | tee -a /workspace/api.log'"
  echo "API restarted on $DEPLOY_HOST (port 8000 inside the container)"
fi
