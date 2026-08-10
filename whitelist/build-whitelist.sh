#!/usr/bin/env bash
#
# Сборка шаблонов подписки Remnawave для работы по белому списку.
#
# На выходе — готовые файлы, которые вставляются в панель
# (Templates → Subscription Templates). Через туннель пойдут только домены
# из domains.txt, остальной трафик — напрямую, мимо VPN.
#
# Запуск:
#   chmod +x build-whitelist.sh
#   ./build-whitelist.sh
#
set -euo pipefail

cd "$(dirname "$0")"

OUT_DIR="./out"
GROUP="→ Remnawave"
DOH="https://1.1.1.1/dns-query"
BASE_LIST="domains.txt"
SINGBOX_LEGACY=0
BLOCK_QUIC=1
EXTRA_FILES=()
EXTRA_URLS=()

RED=$'\e[31m'; GRN=$'\e[32m'; YLW=$'\e[33m'; BLD=$'\e[1m'; RST=$'\e[0m'
info() { echo "${GRN}==>${RST} ${BLD}$*${RST}"; }
warn() { echo "${YLW}[!]${RST} $*"; }
die()  { echo "${RED}[x]${RST} $*" >&2; exit 1; }

usage() {
    cat <<EOF
Использование: ./build-whitelist.sh [опции]

  -o, --out DIR     куда сложить шаблоны (по умолчанию ./out)
  -a, --add FILE    подмешать ещё один файл со списком доменов (можно повторять)
  -u, --url URL     подмешать список по URL (можно повторять)
  -g, --group NAME  имя прокси-группы (по умолчанию "→ Remnawave")
      --dns URL     DoH-резолвер для доменов из списка (по умолчанию ${DOH})
      --singbox-legacy
                    формат DNS для sing-box до 1.12 (старые сборки Hiddify и т.п.).
                    По умолчанию генерируется формат 1.12+, потому что в 1.14
                    старый выпилен совсем.
      --keep-quic   не резать QUIC в TUN-конфиге. По умолчанию QUIC к доменам
                    из списка режется, чтобы браузер откатился на TCP и
                    маршрутизация по домену срабатывала всегда.
  -h, --help        эта справка

Пример:
  ./build-whitelist.sh -a my-domains.txt -o /tmp/templates
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--out)   OUT_DIR="$2"; shift 2 ;;
        -a|--add)   EXTRA_FILES+=("$2"); shift 2 ;;
        -u|--url)   EXTRA_URLS+=("$2"); shift 2 ;;
        -g|--group) GROUP="$2"; shift 2 ;;
        --dns)      DOH="$2"; shift 2 ;;
        --singbox-legacy) SINGBOX_LEGACY=1; shift ;;
        --keep-quic)      BLOCK_QUIC=0; shift ;;
        -h|--help)  usage; exit 0 ;;
        *)          die "Неизвестная опция: $1 (--help)" ;;
    esac
done

command -v python3 >/dev/null 2>&1 || die "Нужен python3: apt install -y python3"
[[ -f "$BASE_LIST" ]] || die "Не нашёл ${BASE_LIST} рядом со скриптом"

# ── Сбор списка ───────────────────────────────────────────────
RAW="$(mktemp)"
trap 'rm -f "$RAW"' EXIT

cat "$BASE_LIST" > "$RAW"

for f in ${EXTRA_FILES[@]+"${EXTRA_FILES[@]}"}; do
    [[ -f "$f" ]] || die "Файл не найден: $f"
    info "Добавляю $f"
    cat "$f" >> "$RAW"
done

for u in ${EXTRA_URLS[@]+"${EXTRA_URLS[@]}"}; do
    info "Качаю $u"
    curl -fsS --max-time 30 "$u" >> "$RAW" || die "Не смог скачать $u"
done

mkdir -p "$OUT_DIR"

# ── Генерация ─────────────────────────────────────────────────
python3 - "$RAW" "$OUT_DIR" "$GROUP" "$DOH" "$SINGBOX_LEGACY" "$BLOCK_QUIC" <<'PYEOF'
import json, os, re, sys

raw_path, out_dir, group, doh = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
singbox_legacy = sys.argv[5] == '1'
block_quic = sys.argv[6] == '1'

# ── Нормализация списка ───────────────────────────────────────
domains = []
seen = set()
bad = []

for line in open(raw_path, encoding='utf-8'):
    line = line.split('#', 1)[0].strip().lower()
    if not line:
        continue
    line = re.sub(r'^[a-z]+://', '', line)   # схема
    line = line.split('/', 1)[0]             # путь
    line = line.split(':', 1)[0]             # порт
    line = line.lstrip('*.').lstrip('.').rstrip('.')
    if not line or line in seen:
        continue
    if not re.fullmatch(r'[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+', line):
        bad.append(line)
        continue
    seen.add(line)
    domains.append(line)

domains.sort()
if not domains:
    sys.exit('Список доменов пуст — проверьте domains.txt')

# Хост DoH-резолвера. Если это literal IP, его гоним в туннель по IP-правилу;
# если имя — добавляем в проксируемые домены, иначе резолвер сам окажется
# заблокирован и белый список работать не будет.
doh_host = re.sub(r'^[a-z]+://', '', doh).split('/', 1)[0].split(':', 1)[0]
doh_is_ip = bool(re.fullmatch(r'\d{1,3}(\.\d{1,3}){3}', doh_host))

xray_domains    = [f'domain:{d}' for d in domains]
singbox_exact   = list(domains)
singbox_suffix  = [f'.{d}' for d in domains]
if not doh_is_ip:
    xray_domains.append(f'domain:{doh_host}')
    singbox_exact.append(doh_host)
    singbox_suffix.append(f'.{doh_host}')

# ── XRAY_JSON: два режима ─────────────────────────────────────
# Happ (и остальные клиенты на Xray) отдают этот JSON ядру как есть — свои
# настройки маршрутизации приложение к нему не применяет. Значит режим
# целиком определяется тем, что мы здесь напишем.
#
#   tun   — весь системный трафик заходит в туннель. Домены восстанавливаются
#           сниффингом, DNS обслуживает сам конфиг.
#   proxy — приложение слушает socks/http, система ходит через них. Имя хоста
#           клиент передаёт сам, DNS остаётся системным.

def xray_inbounds():
    """Инбаунды одинаковые: в режиме TUN приложение заводит системный туннель
    и подаёт трафик в этот же socks."""
    sniff = {'enabled': True, 'routeOnly': False,
             'destOverride': ['http', 'tls', 'quic']}
    return [
        {
            'tag': 'socks', 'port': 10808, 'listen': '127.0.0.1', 'protocol': 'socks',
            'settings': {'udp': True, 'auth': 'noauth'},
            'sniffing': dict(sniff),
        },
        {
            'tag': 'http', 'port': 10809, 'listen': '127.0.0.1', 'protocol': 'http',
            'settings': {'allowTransparent': False},
            'sniffing': dict(sniff),
        },
    ]


def xray_config(mode: str) -> dict:
    # Порядок правил важен: последнее — «всё остальное напрямую». Без него
    # трафик уходил бы в outbound proxy, который панель ставит первым.
    rules = [{'type': 'field', 'protocol': ['bittorrent'], 'outboundTag': 'direct'}]

    if mode == 'tun':
        if block_quic:
            # QUIC к доменам из списка режем, чтобы браузер откатился на TLS
            # поверх TCP. Так маршрутизация по домену срабатывает всегда, а не
            # зависит от того, вытащил ли сниффер SNI из UDP-пакета.
            rules.append({'type': 'field', 'network': 'udp', 'port': '443',
                          'domain': xray_domains, 'outboundTag': 'block'})
        if doh_is_ip:
            # DoH-резолвер гоним в туннель, иначе провайдер подменит ответы
            rules.append({'type': 'field', 'ip': [f'{doh_host}/32'],
                          'outboundTag': 'proxy'})

    rules += [
        {'type': 'field', 'domain': xray_domains, 'outboundTag': 'proxy'},
        {'type': 'field', 'network': 'tcp,udp', 'outboundTag': 'direct'},
    ]

    if mode == 'tun':
        dns = {
            'servers': [
                {'address': doh, 'domains': xray_domains, 'skipFallback': True},
                'localhost',
            ],
            'queryStrategy': 'UseIP',
        }
    else:
        # В proxy-режиме DNS системе не перехватывается: браузер отдаёт имя
        # хоста прямо в socks/http, а резолвит его уже сервер на том конце.
        dns = {'servers': ['localhost'], 'queryStrategy': 'UseIP'}

    return {
        'dns': dns,
        'routing': {
            # AsIs — маршрутизация по имени домена. Имя уезжает на сервер, и он
            # его резолвит сам: подмена DNS у провайдера на список не влияет.
            'domainStrategy': 'AsIs',
            'domainMatcher': 'hybrid',
            'rules': rules,
        },
        'inbounds': xray_inbounds(),
        'outbounds': [
            {'tag': 'direct', 'protocol': 'freedom'},
            {'tag': 'block', 'protocol': 'blackhole'},
        ],
    }

def check_xray(cfg: dict, mode: str) -> None:
    """Инварианты, на которых держится белый список. Ошибка здесь означает
    молча сломанный конфиг у всех пользователей, поэтому падаем сразу."""
    rules = cfg['routing']['rules']

    last = rules[-1]
    if last.get('outboundTag') != 'direct' or 'domain' in last:
        sys.exit(f'{mode}: последним правилом должен быть catch-all в direct, '
                 f'иначе всё уедет в туннель. Получилось: {last}')

    proxy_at = [i for i, r in enumerate(rules)
                if r.get('outboundTag') == 'proxy' and 'domain' in r]
    if len(proxy_at) != 1:
        sys.exit(f'{mode}: ожидалось ровно одно доменное правило в proxy')

    quic_at = [i for i, r in enumerate(rules) if r.get('outboundTag') == 'block']
    if quic_at and quic_at[0] > proxy_at[0]:
        sys.exit(f'{mode}: правило про QUIC стоит после доменного и не сработает')

    if mode == 'proxy' and quic_at:
        sys.exit('proxy: резать QUIC тут нечего — UDP через системный прокси не идёт')


# ── SINGBOX ───────────────────────────────────────────────────
# В 1.12 у sing-box сменился формат DNS-серверов и tun-инбаунда, а в 1.14
# старый убирают совсем. Поэтому по умолчанию пишем новый формат, а старый —
# только по флагу --singbox-legacy.
m = re.match(r'^(?P<scheme>[a-z]+)://(?P<host>[^/:]+)(?::(?P<port>\d+))?(?P<path>/.*)?$', doh)
if not m:
    sys.exit(f'Не разобрал адрес DoH-резолвера: {doh}')
doh_path = m.group('path') or '/dns-query'
doh_port = int(m.group('port')) if m.group('port') else None

if singbox_legacy:
    # Формат до 1.12. Ровно один DoH-сервер через туннель и системный резолвер
    # для всего остального.
    sb_dns_servers = [
        {'tag': 'remote', 'address': doh, 'detour': group},
        {'tag': 'local', 'address': 'local', 'detour': 'direct'},
    ]
else:
    remote_srv = {'type': 'https', 'tag': 'remote', 'server': doh_host, 'detour': group}
    if doh_port:
        remote_srv['server_port'] = doh_port
    if doh_path != '/dns-query':
        remote_srv['path'] = doh_path
    sb_dns_servers = [remote_srv, {'type': 'local', 'tag': 'local'}]

# Инбаунды одинаковые для обоих вариантов: объединённое поле address живёт
# с 1.10, а sniff вынесен в route-правило (inbound sniff убрали в 1.13).
sb_inbounds = [
    {
        'type': 'tun', 'mtu': 9000, 'interface_name': 'tun125', 'tag': 'tun-in',
        'address': ['172.19.0.1/30', 'fdfe:dcba:9876::1/126'],
        'auto_route': True, 'strict_route': True, 'endpoint_independent_nat': True,
        'stack': 'mixed',
        'platform': {'http_proxy': {'enabled': True, 'server': '127.0.0.1',
                                    'server_port': 2412}},
    },
    {
        'type': 'mixed', 'tag': 'mixed-in', 'listen': '127.0.0.1', 'listen_port': 2412,
        'users': [], 'set_system_proxy': False,
    },
]

singbox = {
    'log': {'disabled': True, 'level': 'warn', 'timestamp': True},
    'dns': {
        'servers': sb_dns_servers,
        'rules': [
            {'domain': singbox_exact, 'domain_suffix': singbox_suffix, 'server': 'remote'},
        ],
        'final': 'local',
        'strategy': 'prefer_ipv4',
        'independent_cache': True,
    },
    'inbounds': sb_inbounds,
    'outbounds': [
        # outbounds: null — панель подставит сюда теги серверов, строку не трогать
        {'type': 'selector', 'tag': group, 'interrupt_exist_connections': True,
         'outbounds': None},
        {'type': 'direct', 'tag': 'direct'},
    ],
    'route': {
        'rules': [
            {'action': 'sniff'},
            {'type': 'logical', 'mode': 'or',
             'rules': [{'protocol': 'dns'}, {'port': 53}], 'action': 'hijack-dns'},
            {'ip_is_private': True, 'outbound': 'direct'},
            {'domain': singbox_exact, 'domain_suffix': singbox_suffix, 'outbound': group},
        ],
        # всё, что не попало в белый список — напрямую
        'final': 'direct',
        'auto_detect_interface': True,
        # override_android_vpn здесь намеренно нет: sing-box на десктопе
        # отказывается стартовать с этой опцией.
    },

    'experimental': {
        'clash_api': {
            'external_controller': '127.0.0.1:9090',
            'external_ui': 'yacd',
            'external_ui_download_url':
                'https://github.com/MetaCubeX/Yacd-meta/archive/gh-pages.zip',
            'external_ui_download_detour': 'direct',
            'default_mode': 'rule',
        },
        'cache_file': {'enabled': True, 'path': 'remnawave.db',
                       'cache_id': 'remnawave', 'store_fakeip': True},
    },
}

if not singbox_legacy:
    # 1.12+ требует явно указать, чем резолвить домены при исходящем соединении.
    # Без этого sing-box отказывается стартовать.
    singbox['route']['default_domain_resolver'] = {'server': 'local'}

# ── MIHOMO / CLASH ────────────────────────────────────────────
CLASH_HEAD = """mixed-port: 7890
socks-port: 7891
redir-port: 7892
allow-lan: true
mode: rule
log-level: info
external-controller: 127.0.0.1:9090
dns:
  enable: true
  use-hosts: true
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  default-nameserver:
    - 1.1.1.1
    - 8.8.8.8
  nameserver:
    - 1.1.1.1
    - 8.8.8.8
  fake-ip-filter:
    - '*.lan'
    - stun.*.*.*
    - stun.*.*
    - time.windows.com
    - time.nist.gov
    - time.apple.com
    - time.asia.apple.com
    - '*.openwrt.pool.ntp.org'
    - pool.ntp.org
    - ntp.ubuntu.com
    - time1.apple.com
    - time2.apple.com
    - time3.apple.com
    - time4.apple.com
    - time5.apple.com
    - time6.apple.com
    - time7.apple.com
    - time1.google.com
    - time2.google.com
    - time3.google.com
    - time4.google.com
    - api.joox.com
    - joox.com
    - '*.xiami.com'
    - '*.msftconnecttest.com'
    - '*.msftncsi.com'
    - '+.xboxlive.com'
    - '*.*.stun.playstation.net'
    - xbox.*.*.microsoft.com
    - '*.ipv6.microsoft.com'
    - speedtest.cros.wr.pvp.net

proxies: # LEAVE THIS LINE!

proxy-groups:
  - name: '{group}'
    type: 'select'
    proxies: # LEAVE THIS LINE!

rules:
"""

def clash_yaml(group_name):
    out = [CLASH_HEAD.replace('{group}', group_name)]
    for d in domains:
        out.append(f"  - 'DOMAIN-SUFFIX,{d},{group_name}'\n")
    # MATCH идёт последним: всё, что не совпало, — напрямую
    out.append("  - 'MATCH,DIRECT'\n")
    return ''.join(out)

# ── Запись ────────────────────────────────────────────────────
xray_tun, xray_proxy = xray_config('tun'), xray_config('proxy')
check_xray(xray_tun, 'tun')
check_xray(xray_proxy, 'proxy')

files = {
    'xray-json-tun.json':   json.dumps(xray_tun, ensure_ascii=False, indent=2) + '\n',
    'xray-json-proxy.json': json.dumps(xray_proxy, ensure_ascii=False, indent=2) + '\n',
    'singbox.json':   json.dumps(singbox, ensure_ascii=False, indent=2) + '\n',
    'mihomo.yaml':    clash_yaml(group),
    'clash.yaml':     clash_yaml(group),
}

for name, content in files.items():
    path = os.path.join(out_dir, name)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(content)
    # проверяем, что JSON читается обратно
    if name.endswith('.json'):
        json.loads(content)
    print(f"  {path}  ({len(content)} байт)")

print(f"\nДоменов в списке: {len(domains)}")
if bad:
    print(f"Пропущено непонятных строк: {len(bad)} -> {', '.join(bad[:5])}"
          + (' ...' if len(bad) > 5 else ''))
PYEOF

echo
info "Готово. Дальше — в панель:"
cat <<EOF

  1. Панель → Templates → Subscription Templates
  2. Для каждого типа вставьте содержимое своего файла:

       XRAY_JSON  ←  ${OUT_DIR}/xray-json-tun.json     (обычный VPN, весь трафик)
                  ←  ${OUT_DIR}/xray-json-proxy.json   (только socks/http, без VPN)
       SINGBOX    ←  ${OUT_DIR}/singbox.json
       MIHOMO     ←  ${OUT_DIR}/mihomo.yaml
       CLASH      ←  ${OUT_DIR}/clash.yaml

  Для XRAY_JSON вставляйте ОДИН из двух: tun — если нужен обычный VPN,
  proxy — если система должна ходить через локальный прокси, а Happ не
  поднимал системный туннель.

  3. Сохраните и обновите подписку в клиенте (не просто переподключитесь —
     именно обновите, иначе клиент возьмёт старый конфиг из кеша).

  Проверка: откройте ipinfo.io — должен показать IP ноды (домен в списке),
  и любой сайт не из списка — должен показать ваш домашний IP.
EOF
