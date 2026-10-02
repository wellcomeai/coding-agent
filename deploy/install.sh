#!/bin/bash
# Установка Coding Agent на чистый Ubuntu 24.04 (запускается от root).
#   1. положите заполненный .env в /opt/agent.env (DOMAIN=__AUTO__ — домен <ip>.sslip.io)
#   2. bash deploy/install.sh
# Ставит Docker, запускает сервис и включает автообновление из git-ветки (systemd-таймер).
set -euxo pipefail
APP_DIR=/opt/coding-agent
cd "$(dirname "$0")/.."
[ "$(pwd)" = "$APP_DIR" ] || { echo "Репозиторий должен лежать в $APP_DIR"; exit 1; }

export DEBIAN_FRONTEND=noninteractive
if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
  apt-get update -q
  apt-get install -y -q docker.io docker-compose-v2 docker-buildx git curl
fi
# Docker Hub недоступен из РФ — используем зеркало Timeweb
if [ ! -s /etc/docker/daemon.json ]; then
  echo '{"registry-mirrors": ["https://dockerhub.timeweb.cloud"]}' > /etc/docker/daemon.json
fi
systemctl enable docker
systemctl restart docker

if [ ! -f .env ]; then
  install -m 600 /opt/agent.env .env
fi
if grep -q "__AUTO__" .env; then
  IP=$(curl -4 -s --max-time 10 https://ipv4.icanhazip.com | tr -d '[:space:]')
  DOMAIN="$(echo "$IP" | tr . -).sslip.io"
  sed -i "s/__AUTO__/$DOMAIN/g" .env
fi

install -m 755 deploy/update.sh /usr/local/bin/coding-agent-update
cat > /etc/systemd/system/coding-agent-update.service <<UNIT
[Unit]
Description=Coding Agent: обновление из git
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart=/usr/local/bin/coding-agent-update
UNIT
cat > /etc/systemd/system/coding-agent-update.timer <<UNIT
[Unit]
Description=Coding Agent: проверка обновлений каждые 2 минуты
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload

/usr/local/bin/coding-agent-update --force
systemctl enable --now coding-agent-update.timer
echo "Готово: https://$(grep ^DOMAIN= .env | cut -d= -f2)"
