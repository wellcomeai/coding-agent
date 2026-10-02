#!/bin/bash
# Подтягивает свежий код из текущей ветки и перезапускает сервис, если есть изменения.
# --force — пересобрать и перезапустить даже без новых коммитов.
set -euo pipefail
APP_DIR=/opt/coding-agent
exec 9>/var/lock/coding-agent-update.lock
flock -n 9 || exit 0
cd "$APP_DIR"
BRANCH=$(git rev-parse --abbrev-ref HEAD)
git fetch -q origin "$BRANCH"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$BRANCH")
if [ "$LOCAL" = "$REMOTE" ] && [ "${1:-}" != "--force" ]; then
  exit 0
fi
echo "Обновление $BRANCH: ${LOCAL:0:7} -> ${REMOTE:0:7}"
git reset -q --hard "$REMOTE"
docker compose build
# временный просмотр лога первичной установки занимает порт 80 — освобождаем его для Caddy
pkill -f "http.server 80" 2>/dev/null || true
docker compose up -d --remove-orphans
docker image prune -f >/dev/null
echo "Обновлено до ${REMOTE:0:7}"
