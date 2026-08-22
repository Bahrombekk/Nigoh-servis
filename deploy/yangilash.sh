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

xabar() { echo "[$(date '+%F %T')] $*"; }

# Cron muhiti bo'm-bo'sh: na PATH to'liq, na .env o'zgaruvchilari bor.
# Docker odatda /usr/bin da, lekin compose plagini ba'zi tizimlarda
# /usr/local/bin da — cron'ning tor PATH'i uni topmaydi va yangilanish
# "docker: command not found" bilan jim to'xtaydi.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH"

# PORT .env da turadi (docker-compose env_file orqali konteynerga uzatiladi),
# bu skript esa uni o'qimasdi. Cron'da PORT bo'lmagani uchun salomatlik
# tekshiruvi 8010 ga borardi — serverda esa boshqa port (masalan 23005).
# Natijada muvaffaqiyatli deploy ham "XATO" bo'lib ko'rinadi va skript
# 1 bilan chiqadi.
if [ -z "${PORT:-}" ] && [ -f .env ]; then
    PORT=$(sed -n 's/^[[:space:]]*PORT[[:space:]]*=[[:space:]]*\([0-9][0-9]*\).*/\1/p' .env | tail -1)
fi
PORT=${PORT:-8010}

if ! git fetch origin main --quiet; then
    xabar "XATO: git fetch yiqildi — tarmoq yoki kredensialni tekshiring"
    exit 1
fi
LOKAL=$(git rev-parse HEAD)
MASOFA=$(git rev-parse origin/main)
if [ "$LOKAL" = "$MASOFA" ]; then
    exit 0                       # yangilik yo'q — jim chiqamiz
fi

# Serverda qo'lda tahrirlangan fayl bo'lsa `git pull --ff-only` yiqiladi va
# yangilanish sababi ko'rinmay to'xtab qoladi. Sababni aytib chiqamiz.
if ! git diff --quiet || ! git diff --cached --quiet; then
    xabar "XATO: ish katalogida saqlanmagan o'zgarish bor — pull qilinmaydi:"
    git status --short
    exit 1
fi

xabar "yangilanish: ${LOKAL:0:7} -> ${MASOFA:0:7}"
git pull --ff-only origin main
xabar "build boshlandi"
docker compose up -d --build
xabar "build tugadi, /health kutilmoqda (port $PORT)"

# Ko'tarilganini tekshiramiz — yiqilgan deploy jim qolmasin. Build'dan
# keyin ilova bir necha soniyada ko'tariladi; bitta urinish kam bo'lishi
# mumkin, shuning uchun 30 soniyagacha kutamiz.
for _ in $(seq 1 15); do
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
        xabar "OK — servis tirik (${MASOFA:0:7})"
        exit 0
    fi
    sleep 2
done
xabar "XATO — /health javob bermadi (port $PORT), loglarni ko'ring:"
docker logs --tail 30 nigoh
exit 1
