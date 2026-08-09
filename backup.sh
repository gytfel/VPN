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
ENV_FILE="/opt/remnawave/.env"
# ──────────────────────────────────────────────────────────────

STAMP="$(date +%Y-%m-%d_%H-%M)"
FILE="${BACKUP_DIR}/remnawave_${STAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "[$(date)] Контейнер ${CONTAINER} не запущен" >&2
    exit 1
fi

# Пользователь и база берутся из .env — если их меняли, дамп всё равно снимется
PG_USER="$(grep -m1 '^POSTGRES_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
PG_DB="$(grep -m1 '^POSTGRES_DB=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-postgres}"

docker exec "$CONTAINER" pg_dump -U "$PG_USER" -d "$PG_DB" | gzip > "$FILE"
chmod 600 "$FILE"

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

# Одного дампа для восстановления мало: без .env панель не расшифрует то,
# что лежит в базе. .env намеренно НЕ уходит в Telegram — там секреты.
if [[ -f "$ENV_FILE" ]] && ! cmp -s "$ENV_FILE" "${BACKUP_DIR}/env.backup"; then
    cp "$ENV_FILE" "${BACKUP_DIR}/env.backup"
    chmod 600 "${BACKUP_DIR}/env.backup"
    echo "[$(date)] .env изменился — обновил ${BACKUP_DIR}/env.backup. Скопируйте его с сервера вручную."
fi

# ── Восстановление (вручную, при необходимости) ───────────────
# 1. Положить сохранённый .env в /opt/remnawave/.env (chmod 600) и поднять базу:
#      cd /opt/remnawave && docker compose up -d remnawave-db
# 2. Залить дамп:
#      gunzip -c /opt/remnawave/backups/remnawave_ДАТА.sql.gz \
#        | docker exec -i remnawave-db psql -U postgres -d postgres
# 3. Поднять остальное: docker compose up -d
