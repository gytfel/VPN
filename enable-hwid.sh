#!/usr/bin/env bash
#
# Включение лимита устройств (HWID) в Remnawave.
#
# В ветке 3.x это не переменная окружения, а настройка подписки:
#   PATCH /api/subscription-settings { hwidSettings: {...} }
#
# Запуск на сервере панели:
#   chmod +x enable-hwid.sh
#   ./enable-hwid.sh --limit 3
#
# Посмотреть текущее состояние:  ./enable-hwid.sh --status
# Выключить обратно:            ./enable-hwid.sh --off
#
set -euo pipefail

LIMIT=""
ANNOUNCE="Достигнут лимит устройств. Отключите лишнее устройство или напишите в поддержку."
ACTION="on"

PANEL_URL="${REMNAWAVE_URL:-http://127.0.0.1:3000}"
TOKEN="${REMNAWAVE_TOKEN:-}"
ENV_FILE="${ENV_FILE:-/opt/remnawave/bot/.env}"

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
info() { echo "${GRN}==>${RST} ${BLD}$*${RST}"; }
warn() { echo "${YLW}[!]${RST} $*"; }
die()  { echo "${RED}[x]${RST} $*" >&2; exit 1; }

usage() {
    cat <<EOF
Использование: ./enable-hwid.sh [опции]

  --limit N        сколько устройств разрешено тем, у кого лимит не задан
                   персонально (fallbackDeviceLimit). Обязателен при включении.
  --announce TEXT  что показать клиенту при упоре в лимит (до 200 символов)
  --status         показать текущие настройки и выйти
  --off            выключить проверку HWID
  -h, --help       эта справка

Адрес и токен панели берутся из REMNAWAVE_URL и REMNAWAVE_TOKEN, а если их
нет — из ${ENV_FILE}. Токену нужны права на subscription-settings.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --limit)    LIMIT="$2"; shift 2 ;;
        --announce) ANNOUNCE="$2"; shift 2 ;;
        --status)   ACTION="status"; shift ;;
        --off)      ACTION="off"; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          die "Неизвестная опция: $1 (--help)" ;;
    esac
done

command -v python3 >/dev/null 2>&1 || die "Нужен python3"
command -v curl    >/dev/null 2>&1 || die "Нужен curl"

# ── Токен ─────────────────────────────────────────────────────
if [[ -z "$TOKEN" && -f "$ENV_FILE" ]]; then
    TOKEN="$(grep -m1 '^REMNAWAVE_TOKEN=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
    URL_FROM_ENV="$(grep -m1 '^REMNAWAVE_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
    # адрес из .env бота указывает на docker-сеть, с хоста туда не попасть
    [[ -n "${URL_FROM_ENV}" && "${REMNAWAVE_URL:-}" != "" ]] && PANEL_URL="$REMNAWAVE_URL"
fi
[[ -n "$TOKEN" ]] || die "Нет токена. Задайте REMNAWAVE_TOKEN или положите его в ${ENV_FILE}.
    Токен создаётся в панели: API Tokens → Create."

PANEL_URL="${PANEL_URL%/}"

api() {
    local method="$1" path="$2" body="${3:-}"
    local args=(-sS -X "$method" "${PANEL_URL}${path}"
                -H "Authorization: Bearer ${TOKEN}"
                -H 'Content-Type: application/json'
                -w '\n%{http_code}')
    [[ -n "$body" ]] && args+=(-d "$body")
    curl "${args[@]}"
}

# ── Текущие настройки ─────────────────────────────────────────
RESP="$(api GET /api/subscription-settings)" || die "Панель недоступна по ${PANEL_URL}"
CODE="$(tail -n1 <<<"$RESP")"
BODY="$(sed '$d' <<<"$RESP")"

case "$CODE" in
    200) ;;
    401|403) die "Панель отвергла токен (${CODE}). Нужен токен с правами на subscription-settings." ;;
    000) die "Не достучался до панели по ${PANEL_URL}. С хоста она обычно на http://127.0.0.1:3000" ;;
    *)   die "GET /api/subscription-settings вернул ${CODE}: $(head -c 300 <<<"$BODY")" ;;
esac

show() {
    python3 - "$1" <<'PY'
import json, sys
data = json.loads(sys.argv[1])['response']
hw = data.get('hwidSettings') or {}
state = 'ВКЛЮЧЕН' if hw.get('enabled') else 'выключен'
print(f"  Лимит устройств: {state}")
if hw:
    print(f"  Лимит по умолчанию: {hw.get('fallbackDeviceLimit')}")
    msg = hw.get('maxDevicesAnnounce') or '—'
    print(f"  Сообщение при упоре: {msg}")
PY
}

if [[ "$ACTION" == "status" ]]; then
    info "Текущие настройки"
    show "$BODY"
    exit 0
fi

info "Сейчас"
show "$BODY"

# ── Новое состояние ───────────────────────────────────────────
if [[ "$ACTION" == "on" ]]; then
    [[ -n "$LIMIT" ]] || die "Укажите --limit N — сколько устройств разрешать по умолчанию."
    [[ "$LIMIT" =~ ^[0-9]+$ ]] || die "--limit должен быть целым числом"
    [[ "$LIMIT" -ge 1 ]] || die "--limit 0 означает «никому ничего нельзя» и смысла не имеет.
    Безлимит выдаётся персонально: hwidDeviceLimit=0 у конкретного пользователя."
fi

PAYLOAD="$(python3 - "$BODY" "$ACTION" "${LIMIT:-0}" "$ANNOUNCE" <<'PY'
import json, sys
current = json.loads(sys.argv[1])['response']
action, limit, announce = sys.argv[2], int(sys.argv[3]), sys.argv[4]
hw = current.get('hwidSettings') or {}
payload = {
    'uuid': current['uuid'],
    'hwidSettings': {
        'enabled': action == 'on',
        # при выключении сохраняем прежний лимит, чтобы не потерять настройку
        'fallbackDeviceLimit': limit if action == 'on' else (hw.get('fallbackDeviceLimit') or 1),
        'maxDevicesAnnounce': (announce[:200] if action == 'on'
                               else hw.get('maxDevicesAnnounce')),
    },
}
print(json.dumps(payload, ensure_ascii=False))
PY
)"

RESP="$(api PATCH /api/subscription-settings "$PAYLOAD")"
CODE="$(tail -n1 <<<"$RESP")"
BODY="$(sed '$d' <<<"$RESP")"
[[ "$CODE" == "200" ]] || die "PATCH вернул ${CODE}: $(head -c 300 <<<"$BODY")"

# ── Проверка: перечитываем с сервера, а не верим ответу ───────
RESP="$(api GET /api/subscription-settings)"
BODY="$(sed '$d' <<<"$RESP")"

info "Стало"
show "$BODY"

OK="$(python3 - "$BODY" "$ACTION" <<'PY'
import json, sys
hw = (json.loads(sys.argv[1])['response'].get('hwidSettings') or {})
print('yes' if bool(hw.get('enabled')) == (sys.argv[2] == 'on') else 'no')
PY
)"
[[ "$OK" == "yes" ]] || die "Панель не применила настройку — проверьте права токена."

echo
if [[ "$ACTION" == "on" ]]; then
    info "Готово. Лимит устройств работает."
    cat <<EOF

${BLD}Что важно знать:${RST}

  • Персональный лимит пользователя (hwidDeviceLimit) главнее общего.
    Общий ${LIMIT} применяется только к тем, у кого персональный не задан.

  • hwidDeviceLimit = 0 у пользователя означает ${BLD}безлимит${RST}, а не запрет.

  • Клиенты, которые не умеют HWID, теперь ${BLD}вообще не получат подписку${RST} —
    панель ответит отказом. Happ, v2rayNG и Streisand HWID шлют.
    Проверьте на живом пользователе, прежде чем расходиться клиентам.

  • Список привязанных устройств: панель → пользователь → Devices.
    Оттуда же они удаляются, когда человек меняет телефон.

Откатить: ./enable-hwid.sh --off
EOF
else
    info "Лимит устройств выключен."
fi
