#!/usr/bin/env bash
# Build the web client, sync the repository to the server, install the backend and restart the API.
#
# Configure in .env.deploy (git-ignored), see .env.example:
#   DEPLOY_HOST=user@1.2.3.4  DEPLOY_PORT=22  DEPLOY_KEY=~/.ssh/key  DEPLOY_DIR=/srv/seekin/app
#   DEPLOY_VENV=/srv/seekin/venv  DEPLOY_CACHE=/srv/seekin/cache
#   DEPLOY_SERVICE=seekin-api   # restart this systemd unit; leave unset to run uvicorn in tmux (dev server)
# Usage: scripts/deploy.sh [--no-restart] [--no-build]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.deploy ] && . ./.env.deploy
: "${DEPLOY_HOST:?set DEPLOY_HOST in .env.deploy}"
PORT="${DEPLOY_PORT:-22}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/id_ed25519}"
DIR="${DEPLOY_DIR:-/workspace/app}"
VENV="${DEPLOY_VENV:-/workspace/venv}"
CACHE="${DEPLOY_CACHE:-/workspace/cache}"
SERVICE="${DEPLOY_SERVICE:-}"
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -p "$PORT")
RESTART=1 BUILD=1
for arg in "$@"; do
  case "$arg" in
    --no-restart) RESTART=0 ;;
    --no-build) BUILD=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

# The API serves web/dist, so build it here and ship it with the rest of the tree.
if [ "$BUILD" = 1 ]; then
  [ -d web/node_modules ] || npm --prefix web ci --no-audit --no-fund
  npm --prefix web run build
fi

rsync -az --delete -e "ssh -i $KEY -o IdentitiesOnly=yes -p $PORT" \
  --exclude .git --exclude node_modules --exclude .venv --exclude __pycache__ \
  --exclude .claude --exclude '.env*' --exclude '.DS_Store' ./ "$DEPLOY_HOST:$DIR/"

"${SSH[@]}" "$DEPLOY_HOST" "set -e; . $VENV/bin/activate; uv pip install -q -e '$DIR/backend[dev]'"

[ "$RESTART" = 1 ] || exit 0
if [ -n "$SERVICE" ]; then
  "${SSH[@]}" "$DEPLOY_HOST" "sudo systemctl restart $SERVICE && sleep 3 && systemctl is-active $SERVICE"
  echo "API restarted on $DEPLOY_HOST (systemd unit $SERVICE)"
else
  "${SSH[@]}" "$DEPLOY_HOST" "tmux kill-session -t api 2>/dev/null || true; \
    tmux new-session -d -s api 'cd $DIR && . $VENV/bin/activate && SPHEREX_CACHE_DIR=$CACHE SPHEREX_LOG_LEVEL=${DEPLOY_LOG_LEVEL:-INFO} \
    uvicorn spherex_api.main:app --host 0.0.0.0 --port 8000 --no-access-log 2>&1 | tee -a $(dirname "$DIR")/api.log'"
  echo "API restarted on $DEPLOY_HOST (port 8000, tmux session api)"
fi
