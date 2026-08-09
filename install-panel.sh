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

# Порты SSH. Ubuntu 24.04 держит настройки в /etc/ssh/sshd_config.d/*.conf,
# один только sshd_config читать нельзя — запрёте себя снаружи.
ssh_ports() {
    local ports=''
    if command -v sshd >/dev/null 2>&1; then
        # sshd -T разворачивает все Include — самый надёжный источник
        ports="$(sshd -T 2>/dev/null | awk '/^port /{print $2}' || true)"
    fi
    if [[ -z "$ports" ]]; then
        ports="$(grep -rhoP '^\s*Port\s+\K[0-9]+' \
                 /etc/ssh/sshd_config /etc/ssh/sshd_config.d/ 2>/dev/null || true)"
    fi
    # Порт текущей сессии — последняя страховка, если конфиг прочитать не вышло
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        ports+=$'\n'"$(awk '{print $4}' <<<"$SSH_CONNECTION")"
    fi
    [[ -n "${ports//[[:space:]]/}" ]] || ports=22
    tr ' ' '\n' <<<"$ports" | grep -E '^[0-9]+$' | sort -un
}

mapfile -t SSH_PORTS < <(ssh_ports)

if ! command -v ufw >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq ufw
fi
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
for p in "${SSH_PORTS[@]}"; do
    ufw allow "${p}/tcp" >/dev/null
done
ufw allow 80/tcp  >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
info "Открыты порты: ${SSH_PORTS[*]}, 80, 443."
info "Панель слушает 127.0.0.1:3000 — наружу не торчит (это делает сам compose, не ufw)."

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

# Правит ключ в .env и проверяет, что запись прошла. Если апстрим переименует
# ключ, скрипт должен упасть здесь, а не оставить панель с дефолтным секретом.
set_env() {
    local key="$1" val="$2"
    grep -qE "^${key}=" .env \
        || die "В .env нет ключа ${key}. Апстрим сменил формат .env.sample — обновите скрипт."
    sed -i "s|^${key}=.*|${key}=${val}|" .env
    [[ "$(grep -m1 -E "^${key}=" .env)" == "${key}=${val}" ]] \
        || die "Не удалось записать ${key} в .env"
}

info "Генерирую секреты"
set_env APP_SECRET            "$(openssl rand -hex 64)"
set_env METRICS_PASS          "$(openssl rand -hex 64)"
set_env WEBHOOK_SECRET_HEADER "$(openssl rand -hex 32)"   # только [a-zA-Z0-9], минимум 32 символа

PG_PW="$(openssl rand -hex 24)"
set_env POSTGRES_PASSWORD "${PG_PW}"

grep -qE '^DATABASE_URL=' .env || die "В .env нет DATABASE_URL — обновите скрипт."
sed -i "s|^\(DATABASE_URL=\"postgresql://postgres:\)[^@]*\(@.*\)|\1${PG_PW}\2|" .env
grep -qE "^DATABASE_URL=\"postgresql://postgres:${PG_PW}@" .env \
    || die "Пароль не попал в DATABASE_URL — панель не поднимется. Проверьте формат строки в .env."

info "Прописываю домены"
set_env FRONT_END_DOMAIN  "${PANEL_DOMAIN}"
set_env PANEL_DOMAIN      "${PANEL_DOMAIN}"   # используется в ссылках из Telegram-уведомлений
set_env SUB_PUBLIC_DOMAIN "${SUB_DOMAIN}"

chmod 600 .env

info "Поднимаю панель"
docker compose up -d

# У контейнера remnawave есть healthcheck — ждём его, а не «на глазок»
info "Жду, пока панель станет healthy (до 3 минут)"
for _ in {1..90}; do
    state="$(docker inspect -f '{{.State.Health.Status}}' remnawave 2>/dev/null || echo 'starting')"
    [[ "$state" == "healthy" ]] && break
    if [[ "$(docker inspect -f '{{.State.Status}}' remnawave 2>/dev/null || echo '')" == "exited" ]]; then
        docker compose logs --tail 40 remnawave
        die "Контейнер remnawave упал. Логи выше — почти всегда дело в .env."
    fi
    sleep 2
done
[[ "$state" == "healthy" ]] || warn "Панель ещё не healthy. Продолжаю, но проверьте: docker compose logs -f"

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

# ── Самопроверка ──────────────────────────────────────────────
info "Проверяю, что получилось"

if docker exec caddy caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
    echo "  [ok] Caddyfile валиден"
else
    warn "Caddy не принял конфиг: cd ${CADDY_DIR} && docker compose logs -f"
fi

# Панель через Caddy изнутри docker-сети: фильтр по IP тут не мешает
if docker exec caddy wget -qO- --timeout=5 http://remnawave:3000/api/auth/status >/dev/null 2>&1 \
   || docker exec caddy wget -qO- --timeout=5 http://remnawave:3000 >/dev/null 2>&1; then
    echo "  [ok] Caddy видит панель по http://remnawave:3000"
else
    warn "Caddy не достучался до панели. Проверьте сеть remnawave-network и логи панели."
fi

# Маскировка: голый IP должен отдавать пустой 204
if [[ -n "$SERVER_IP" ]]; then
    code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 8 "https://${SERVER_IP}" || echo '000')"
    if [[ "$code" == "204" ]]; then
        echo "  [ok] Запрос на голый IP отдаёт 204 — маскировка работает"
    else
        warn "Голый IP ответил ${code} вместо 204 — проверьте секцию :443 в Caddyfile"
    fi
fi

# Сертификат на домене панели. Может не успеть выпуститься — это не ошибка.
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 12 "https://${PANEL_DOMAIN}" || echo '000')"
case "$code" in
    200|301|302) echo "  [ok] https://${PANEL_DOMAIN} отвечает ${code}" ;;
    204)         echo "  [ok] https://${PANEL_DOMAIN} отдал 204 — вы не в списке ADMIN_IPS (фильтр работает)" ;;
    000)         warn "https://${PANEL_DOMAIN} пока не отвечает. Сертификат выпускается 10-60 секунд, подождите и проверьте в браузере." ;;
    *)           warn "https://${PANEL_DOMAIN} ответил ${code}. Логи: cd ${CADDY_DIR} && docker compose logs -f" ;;
esac

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
