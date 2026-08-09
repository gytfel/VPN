#!/usr/bin/env bash
#
# Бэкап базы Remnawave + отправка в Telegram
#
# Установка:
#   cp backup.sh /opt/remnawave/backup.sh && chmod +x /opt/remnawave/backup.sh
#   nano /opt/remnawave/backup.sh   # вписать TG_TOKEN и TG_CHAT
#   crontab -e
#   0 4 * * * /opt/remnawave/backup.sh >> /var/log/rw-backup.log 2>&1
#
set -euo pipefail

# ── Настройки ─────────────────────────────────────────────────
TG_TOKEN=""          # токен бота от @BotFather (можно отдельного, служебного)
TG_CHAT=""           # ваш chat_id, узнать у @userinfobot
BACKUP_DIR="/opt/remnawave/backups"
KEEP_DAYS=14
CONTAINER="remnawave-db"
# ──────────────────────────────────────────────────────────────

STAMP="$(date +%Y-%m-%d_%H-%M)"
FILE="${BACKUP_DIR}/remnawave_${STAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "[$(date)] Контейнер ${CONTAINER} не запущен" >&2
    exit 1
fi

docker exec "$CONTAINER" pg_dump -U postgres -d postgres | gzip > "$FILE"

SIZE_BYTES="$(stat -c%s "$FILE")"
SIZE_HUMAN="$(du -h "$FILE" | cut -f1)"

if [[ "$SIZE_BYTES" -lt 1024 ]]; then
    echo "[$(date)] Дамп подозрительно мал (${SIZE_BYTES}b) — проверьте базу" >&2
    exit 1
fi

echo "[$(date)] Бэкап готов: ${FILE} (${SIZE_HUMAN})"

# Отправка в Telegram (лимит Bot API — 50 МБ)
if [[ -n "$TG_TOKEN" && -n "$TG_CHAT" ]]; then
    if [[ "$SIZE_BYTES" -lt 52428800 ]]; then
        curl -fsS -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendDocument" \
            -F chat_id="${TG_CHAT}" \
            -F document=@"${FILE}" \
            -F caption="Remnawave backup ${STAMP} (${SIZE_HUMAN})" \
            >/dev/null && echo "[$(date)] Отправлено в Telegram"
    else
        curl -fsS -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
            -d chat_id="${TG_CHAT}" \
            -d text="Бэкап ${STAMP} весит ${SIZE_HUMAN} — больше лимита Telegram. Лежит на сервере: ${FILE}" \
            >/dev/null
    fi
fi

# Чистка старых
find "$BACKUP_DIR" -name 'remnawave_*.sql.gz' -mtime "+${KEEP_DAYS}" -delete
echo "[$(date)] Готово. Храню последние ${KEEP_DAYS} дней."

# ── Восстановление (вручную, при необходимости) ───────────────
# gunzip -c /opt/remnawave/backups/remnawave_ДАТА.sql.gz \
#   | docker exec -i remnawave-db psql -U postgres -d postgres
