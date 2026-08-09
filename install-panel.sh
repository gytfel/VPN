#!/usr/bin/env bash
#
# Remnawave Panel + Caddy — установка сервера панели (шаг 1)
#
# Запуск:
#   chmod +x install-panel.sh
#   sudo ./install-panel.sh
#
# Или без вопросов:
#   sudo PANEL_DOMAIN=panel.example.com SUB_DOMAIN=sub.example.com \
#        ADMIN_IPS="1.2.3.4" ./install-panel.sh
#
set -euo pipefail

# ─────────────────────────────────────────────────────────────
#  НАСТРОЙКИ — можно задать здесь или ответить на вопросы
# ─────────────────────────────────────────────────────────────
PANEL_DOMAIN="${PANEL_DOMAIN:-}"   # panel.example.com
SUB_DOMAIN="${SUB_DOMAIN:-}"       # sub.example.com
ADMIN_IPS="${ADMIN_IPS:-}"         # "1.2.3.4 5.6.7.0/24", пусто = без IP-фильтра

INSTALL_DIR="/opt/remnawave"
CADDY_DIR="${INSTALL_DIR}/caddy"

# ─────────────────────────────────────────────────────────────

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
info() { echo "${GRN}==>${RST} ${BLD}$*${RST}"; }
warn() { echo "${YLW}[!]${RST} $*"; }
die()  { echo "${RED}[x]${RST} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Запускайте от root: sudo ./install-panel.sh"

# ── Защита от повторного запуска ──────────────────────────────
if [[ -f "${INSTALL_DIR}/.env" ]]; then
    die "${INSTALL_DIR}/.env уже существует.
    Повторный запуск перегенерирует секреты и оторвёт панель от базы.
    Если нужна чистая переустановка — сначала:
      cd ${INSTALL_DIR} && docker compose down -v && cd / && rm -rf ${INSTALL_DIR}"
fi

# ── Опрос ─────────────────────────────────────────────────────
if [[ -z "$PANEL_DOMAIN" ]]; then
    read -rp "Домен панели (напр. panel.example.com): " PANEL_DOMAIN
fi
if [[ -z "$SUB_DOMAIN" ]]; then
    read -rp "Домен страницы подписки (напр. sub.example.com): " SUB_DOMAIN
fi
if [[ -z "$ADMIN_IPS" ]]; then
    echo
    echo "IP, с которых будет открываться админка (через пробел)."
    echo "Свой текущий IP: $(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '?')"
    echo "Пусто = без IP-фильтра (тогда обязательно включите Passkey в панели)."
    read -rp "Разрешённые IP: " ADMIN_IPS
fi

[[ -n "$PANEL_DOMAIN" ]] || die "Домен панели обязателен"
[[ -n "$SUB_DOMAIN" ]]   || die "Домен подписки обязателен"

if [[ "$PANEL_DOMAIN" == "$SUB_DOMAIN" ]]; then
    die "Домены панели и подписки должны быть разными, иначе IP-фильтр отрежет клиентов."
fi

# ── Проверка DNS ──────────────────────────────────────────────
SERVER_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '')"
RESOLVED="$(getent hosts "$PANEL_DOMAIN" 2>/dev/null | awk '{print $1}' | head -1 || echo '')"

if [[ -z "$RESOLVED" ]]; then
    warn "$PANEL_DOMAIN не резолвится. Создайте A-запись и подождите."
    read -rp "Продолжить всё равно? [y/N] " a; [[ "$a" =~ ^[Yy]$ ]] || exit 1
elif [[ -n "$SERVER_IP" && "$RESOLVED" != "$SERVER_IP" ]]; then
    warn "$PANEL_DOMAIN → $RESOLVED, а IP сервера $SERVER_IP."
    warn "Если включён оранжевый значок Cloudflare — выключите его до выпуска сертификата."
    warn "ВАЖНО: при оранжевом значке IP-фильтр в Caddy видит адреса Cloudflare, а не ваш."
    read -rp "Продолжить? [y/N] " a; [[ "$a" =~ ^[Yy]$ ]] || exit 1
fi

# ── Фаервол ───────────────────────────────────────────────────
info "Настраиваю фаервол"
SSH_PORT="$(grep -oP '^\s*Port\s+\K[0-9]+' /etc/ssh/sshd_config 2>/dev/null | head -1 || true)"
SSH_PORT="${SSH_PORT:-22}"

if ! command -v ufw >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq ufw
fi
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow "${SSH_PORT}/tcp" >/dev/null
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
info "Открыты порты: ${SSH_PORT}, 80, 443. Порт 3000 наружу закрыт."

# ── Docker ────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    info "Ставлю Docker"
    curl -fsSL https://get.docker.com | sh
else
    info "Docker уже есть — пропускаю"
fi

# ── Панель ────────────────────────────────────────────────────
info "Скачиваю конфиги панели"
mkdir -p "$INSTALL_DIR" && cd "$INSTALL_DIR"
curl -fsS -o docker-compose.yml \
    https://raw.githubusercontent.com/remnawave/backend/refs/heads/main/docker-compose-prod.yml
curl -fsS -o .env \
    https://raw.githubusercontent.com/remnawave/backend/refs/heads/main/.env.sample

info "Генерирую секреты"
sed -i "s/^APP_SECRET=.*/APP_SECRET=$(openssl rand -hex 64)/" .env
sed -i "s/^METRICS_PASS=.*/METRICS_PASS=$(openssl rand -hex 64)/" .env
sed -i "s/^WEBHOOK_SECRET_HEADER=.*/WEBHOOK_SECRET_HEADER=$(openssl rand -hex 64)/" .env

PG_PW="$(openssl rand -hex 24)"
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=${PG_PW}/" .env
sed -i "s|^\(DATABASE_URL=\"postgresql://postgres:\)[^\@]*\(@.*\)|\1${PG_PW}\2|" .env

info "Прописываю домены"
sed -i "s|^FRONT_END_DOMAIN=.*|FRONT_END_DOMAIN=${PANEL_DOMAIN}|" .env
sed -i "s|^SUB_PUBLIC_DOMAIN=.*|SUB_PUBLIC_DOMAIN=${SUB_DOMAIN}|" .env

chmod 600 .env

info "Поднимаю панель"
docker compose up -d
sleep 10

# ── Caddy ─────────────────────────────────────────────────────
info "Настраиваю Caddy"
mkdir -p "$CADDY_DIR"

if [[ -n "$ADMIN_IPS" ]]; then
    IP_BLOCK="    @blocked not remote_ip ${ADMIN_IPS}
    respond @blocked 204
"
else
    IP_BLOCK="    # IP-фильтр выключен. Включите Passkey в Remnawave Settings.
"
fi

cat > "${CADDY_DIR}/Caddyfile" <<CADDYEOF
https://${PANEL_DOMAIN} {
${IP_BLOCK}
    encode
    reverse_proxy * http://remnawave:3000
}

# Маскировка: запрос на голый IP отдаёт пустой 204
:443 {
    tls internal
    respond 204
}
CADDYEOF

cat > "${CADDY_DIR}/docker-compose.yml" <<'CADDYCOMPOSE'
services:
  caddy:
    image: caddy:2.9
    container_name: 'caddy'
    hostname: caddy
    restart: always
    ports:
      - '0.0.0.0:443:443'
      - '0.0.0.0:80:80'
    networks:
      - remnawave-network
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy-ssl-data:/data

networks:
  remnawave-network:
    name: remnawave-network
    driver: bridge
    external: true

volumes:
  caddy-ssl-data:
    driver: local
    external: false
    name: caddy-ssl-data
CADDYCOMPOSE

cd "$CADDY_DIR" && docker compose up -d
sleep 5

# ── Итог ──────────────────────────────────────────────────────
echo
info "Готово."
echo
echo "  Панель:    ${BLD}https://${PANEL_DOMAIN}${RST}"
echo "  Подписка:  ${SUB_DOMAIN} (поднимем на шаге 5)"
if [[ -n "$ADMIN_IPS" ]]; then
    echo "  Доступ:    только с ${ADMIN_IPS}"
else
    echo "  Доступ:    ${YLW}открыт всем — включите Passkey в настройках${RST}"
fi
echo
echo "${BLD}Сейчас же:${RST}"
echo "  1. Откройте панель и зарегистрируйтесь — первый юзер станет super-admin."
echo "  2. Проверьте версию: должна быть 2.7.5 или выше."
echo
echo "${BLD}Полезное:${RST}"
echo "  Логи панели:   cd ${INSTALL_DIR} && docker compose logs -f"
echo "  Логи Caddy:    cd ${CADDY_DIR} && docker compose logs -f"
echo "  Если заперлись: docker exec -it remnawave remnawave"
echo
