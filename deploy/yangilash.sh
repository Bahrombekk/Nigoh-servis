#!/usr/bin/env bash
# Nigoh'ni serverda yangilash (pull-based deploy).
#
# GitHub Actions'dagi CI faqat test/build qiladi — serverga o'zi hech
# narsa yubormaydi. Bu skript teskarisini qiladi: server o'zi GitHub'dan
# yangilikni tortadi. Cron'ga qo'yilsa har 5 daqiqada tekshiradi va
# faqat yangi commit bo'lsa qayta build qiladi:
#
#   sudo crontab -e
#   */5 * * * * /opt/nigoh/deploy/yangilash.sh >> /var/log/nigoh-deploy.log 2>&1
#
# Server o'chib yonsa servis o'zi ko'tariladi (docker-compose'da
# restart: unless-stopped bor) — faqat Docker yoqilgan bo'lsin:
#   sudo systemctl enable docker
set -euo pipefail
cd "$(dirname "$0")/.."

git fetch origin main --quiet
LOKAL=$(git rev-parse HEAD)
MASOFA=$(git rev-parse origin/main)
if [ "$LOKAL" = "$MASOFA" ]; then
    exit 0                       # yangilik yo'q — jim chiqamiz
fi

echo "[$(date '+%F %T')] yangilanish: ${LOKAL:0:7} -> ${MASOFA:0:7}"
git pull --ff-only origin main
docker compose up -d --build

# Ko'tarilganini tekshiramiz — yiqilgan deploy jim qolmasin.
sleep 8
if curl -fsS "http://127.0.0.1:${PORT:-8010}/health" >/dev/null; then
    echo "[$(date '+%F %T')] OK — servis tirik"
else
    echo "[$(date '+%F %T')] XATO — /health javob bermadi, loglarni ko'ring:"
    docker logs --tail 30 nigoh
    exit 1
fi
