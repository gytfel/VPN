# Пошаговый запуск VPN с нуля

Инструкция от пустой VPS до работающего VPN с оплатой в Telegram. Делайте
подряд, не пропуская проверки — каждая ловит ошибку там, где её ещё дёшево
чинить.

Первые семь шагов дают работающий VPN. Остальное — продажи и удобство.

---

## Шаг 0. Что купить и подготовить

**Два сервера.**

| | Панель | Нода |
|---|---|---|
| Ядра / память | 2 / 4 ГБ | 1–2 / 2 ГБ |
| Диск | 20 ГБ | 10 ГБ |
| ОС | Ubuntu 24.04 | Ubuntu 24.04 |
| Что важно | стабильность, цена | канал и локация |

Через панель трафик пользователей не идёт — экономьте на ней, а не на ноде.

**Домен.** Обязателен. Remnawave не работает на подпути вроде
`/remnawave` — только на корне домена или поддомена.

**Полчаса времени** и доступ по SSH к обоим серверам.

### DNS

Две A-записи на IP **сервера панели**:

```
panel.example.com  →  IP панели
sub.example.com    →  IP панели
```

Если домен на Cloudflare — **серый значок (DNS only) на обе записи** на время
установки. С оранжевым Caddy не выпустит сертификат, а фильтр по IP пропустит
кого угодно.

**Проверка.** На сервере панели:

```bash
getent hosts panel.example.com
getent hosts sub.example.com
```

Обе команды должны вернуть IP сервера панели. Не вернули — подождите,
DNS расходится до получаса.

---

## Шаг 1. Сервер панели

На сервере панели:

```bash
git clone https://github.com/gytfel/VPN.git && cd VPN
chmod +x install-panel.sh
sudo ./install-panel.sh
```

Скрипт спросит три вещи:

| Вопрос | Что отвечать |
|---|---|
| Домен панели | `panel.example.com` |
| Домен подписки | `sub.example.com` |
| Разрешённые IP | ваш домашний IP (скрипт его подскажет) |

Дальше он всё сделает сам: фаервол, Docker, панель, секреты, Caddy — и в конце
сам себя проверит.

> **Домашний IP динамический?** Оставьте поле пустым и обязательно включите
> Passkey в настройках панели после регистрации. Иначе будете запираться
> снаружи каждые пару дней.

### Немедленно: регистрация

Откройте `https://panel.example.com` и **зарегистрируйтесь прямо сейчас**.
Первый зарегистрировавшийся становится super-admin. Пока вы этого не сделали,
им может стать любой, кто найдёт домен.

### Проверка шага 1

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://panel.example.com   # 200
curl -sk -o /dev/null -w '%{http_code}\n' https://ВАШ_IP_ПАНЕЛИ      # 204
docker ps --format '{{.Names}}'    # remnawave, remnawave-db, remnawave-redis, caddy
```

Второй запрос отдаёт `204` — значит маскировка работает: кто стучится на голый
IP, видит пустоту.

Не отвечает? Сертификат выпускается до минуты. Если дольше:

```bash
cd /opt/remnawave/caddy && docker compose logs -f
```

Почти всегда это оранжевый значок Cloudflare или неразошедшийся DNS.

---

## Шаг 2. Config Profile — конфиг Xray для нод

Профиль — это конфиг Xray, который панель разошлёт на ноды. Делаем его
**до** ноды: форма создания ноды требует выбрать профиль, раньше её не создать.

### 2.1. Ключи Reality

Нужны приватный ключ, публичный ключ и short ID. Генерирует их сам Xray —
скачайте бинарник на любой из серверов:

```bash
cd /tmp
curl -fsSLo xray.zip https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip
unzip -o xray.zip xray && chmod +x xray
./xray x25519
openssl rand -hex 8
```

Вывод будет такой:

```
PrivateKey: SOYwQMkoq0Bpy9IEyP8ZpS72ymUjL5oL_mxNaIgj4nA
Password (PublicKey): gSFAVBEQEIP_l4s2pD1PDtrYNtcSU7UuBgvp9w_7UA8
```

Выпишите все три значения — приватный ключ, публичный и short ID. Приватный
пойдёт в профиль на сервере, публичный — в Host на шаге 5, и они обязаны быть
парой из **одного** запуска.

> Если в редакторе профиля панели есть кнопка генерации ключей — можно нажать
> её, результат тот же. Позже, когда нода поднимется, та же команда доступна
> как `docker exec remnanode xray x25519`.

`unzip` нет — поставьте: `apt install -y unzip`.

### 2.2. Маскировочный домен

Reality выдаёт вашу ноду за чужой сайт. Этот сайт должен быть:

- быстрым и рядом с нодой географически
- с TLS 1.3 и HTTP/2
- не заблокированным в вашей стране
- **не за Cloudflare**

Рабочие варианты: `cdn.jsdelivr.net`, `www.lovelive-anime.jp`, сайты крупных
CDN. Не берите домены Google, Apple и Microsoft — их слишком много кто уже
использует.

### 2.3. Создание профиля

Панель → **Config Profiles** → создать. Вставьте
[configs/vless-reality.json](configs/vless-reality.json) и замените четыре
плейсхолдера:

| Плейсхолдер | На что менять |
|---|---|
| `ЗАМЕНИТЕ-НА-МАСКИРОВОЧНЫЙ-ДОМЕН:443` | `cdn.jsdelivr.net:443` |
| `ЗАМЕНИТЕ-НА-МАСКИРОВОЧНЫЙ-ДОМЕН` | `cdn.jsdelivr.net` |
| `ЗАМЕНИТЕ-НА-PRIVATE-KEY` | `PrivateKey` из 2.1 |
| `ЗАМЕНИТЕ-НА-SHORT-ID` | short ID из 2.1 |

Что в этом конфиге уже сделано за вас:

- VLESS + Reality на 443, `flow: xtls-rprx-vision` панель проставит клиентам сама
- торренты режутся (`bittorrent` → `BLOCK`) — иначе прилетит абуза от хостера
- закрыт доступ к приватным сетям и метаданным облака (`169.254.169.254`),
  чтобы через вашу ноду не лазили во внутреннюю сеть сервера

### Проверка шага 2

Профиль сохранился без ошибок, в списке инбаундов виден `VLESS-REALITY`.

---

## Шаг 3. Сервер ноды

На **сервере ноды**.

### 3.1. Фаервол

Порт `2222` — служебный API ноды. Он открывается **только для IP панели**:

```bash
apt update && apt install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 443/tcp
ufw allow from IP_ПАНЕЛИ to any port 2222 proto tcp
ufw --force enable
```

Замените `IP_ПАНЕЛИ` на реальный адрес. Открыть 2222 всему миру — значит
отдать управление нодой кому угодно.

### 3.2. Docker

```bash
command -v docker || curl -fsSL https://get.docker.com | sh
```

---

## Шаг 4. Нода в панели

Панель → **Nodes** → создать:

| Поле | Что вписать |
|---|---|
| Name | как вам удобно, например `NL-1` |
| Address | IP сервера ноды |
| Port | `2222` |
| Country | код страны, например `NL` |
| Config Profile | профиль из шага 2 |
| Inbounds | отметить `VLESS-REALITY` |

После сохранения панель покажет готовый `docker-compose.yml` для ноды — с уже
подставленным `SECRET_KEY`. Скопируйте его на сервер ноды:

```bash
mkdir -p /opt/remnanode && cd /opt/remnanode
nano docker-compose.yml     # вставить то, что показала панель
docker compose up -d
```

Нода работает в `network_mode: host` — так Xray слушает 443 напрямую, без
проброса портов.

### Проверка шага 4

В панели нода загорается зелёным в течение минуты. Заодно на сервере ноды:

```bash
docker logs remnanode --tail 30      # без ошибок, Xray стартовал
ss -tlnp | grep :443                 # порт слушается
```

Не зеленеет — это почти всегда фаервол. С сервера панели:

```bash
nc -zv IP_НОДЫ 2222
```

---

## Шаг 5. Host — как клиент увидит ноду

Нода — это сервер. Host — то, что окажется в конфиге у клиента.

Панель → **Hosts** → создать:

| Поле | Что вписать |
|---|---|
| Config Profile / Inbound | профиль из шага 2, инбаунд `VLESS-REALITY` |
| Address | IP ноды (или домен, если он на неё указывает) |
| Port | `443` |
| SNI | ваш маскировочный домен из 2.2 |
| Public Key | `Password (PublicKey)` из 2.1 |
| Short ID | short ID из 2.1 |
| Remark | название, которое человек увидит в списке серверов |

Здесь легко ошибиться: `Public Key`, а не `PrivateKey`. Приватный остался в
профиле на сервере, наружу он не уходит никогда.

---

## Шаг 6. Internal Squad — это ваш тариф

Панель → **Internal Squads** → создать. Отметьте инбаунд `VLESS-REALITY`.

Сквад — набор инбаундов, к которым пользователь получает доступ. Тариф «одна
страна» и тариф «все страны» — это два разных сквада.

**Запишите UUID сквада.** Он понадобится боту в `REMNAWAVE_SQUAD_UUID`. Без
сквада пользователь создастся, но доступа к нодам у него не будет.

---

## Шаг 7. Первое подключение

Момент истины.

1. Панель → **Users** → создать. Имя любое, срок — неделя, сквад из шага 6.
2. Откройте пользователя, скопируйте **Subscription URL**.
3. Поставьте [Happ](https://apps.apple.com/app/happ-proxy-utility/id6783623643)
   на телефон, добавьте подписку по этой ссылке, подключитесь.
4. Откройте `https://ipinfo.io` — должен показать **IP вашей ноды**.

Показал IP ноды — **VPN работает**. Дальше только надстройки.

### Если не подключается

| Симптом | Куда смотреть |
|---|---|
| Клиент не видит сервер | 443 закрыт на ноде, или в Host не тот адрес |
| Подключается и сразу рвётся | не совпали Public Key / Short ID / SNI между профилем и Host |
| Пусто в списке серверов | пользователь не в скваде, или в скваде нет инбаунда |
| Всё зелено, но интернета нет | `docker logs remnanode --tail 50` на ноде |

Проверить связку ключей: приватный в профиле и публичный в Host должны быть
парой из **одного** запуска `xray x25519`.

---

## Шаг 8. Страница подписки

Без неё клиент получает только голую ссылку. Страница даёт человеческий вид
с кнопками установки приложений под каждую платформу.

На сервере панели:

```bash
mkdir -p /opt/remnawave/subscription-page && cd /opt/remnawave/subscription-page
```

`.env`:

```
APP_PORT=3010
REMNAWAVE_PANEL_URL=http://remnawave:3000
REMNAWAVE_API_TOKEN=ТОКЕН
TRUST_PROXY=1
```

Токен: панель → **API Tokens** → создать.

`docker-compose.yml`:

```yaml
services:
  remnawave-subscription-page:
    image: remnawave/subscription-page:latest
    container_name: remnawave-subscription-page
    hostname: remnawave-subscription-page
    restart: always
    env_file: .env
    networks:
      - remnawave-network

networks:
  remnawave-network:
    name: remnawave-network
    external: true
```

```bash
docker compose up -d
```

`TRUST_PROXY=1` обязателен: страница стоит за Caddy, без него она будет
видеть IP контейнера вместо IP клиента. Порт наружу не публикуем — до неё
ходит только Caddy по внутренней сети.

---

## Шаг 9. Telegram-бот с оплатой

Заранее получите:

- токен бота — [@BotFather](https://t.me/BotFather) → `/newbot`
- токен CryptoBot — [@CryptoBot](https://t.me/CryptoBot) → Crypto Pay → Create App
- API-токен панели — панель → API Tokens
- UUID сквада из шага 6

```bash
cp -r ~/VPN/bot /opt/remnawave/bot && cd /opt/remnawave/bot
cp .env.example .env
nano .env             # четыре значения выше + PUBLIC_BASE=https://sub.example.com
nano tariffs.json     # свои цены и сроки
docker compose up -d --build
```

Новым пользователям бот первой кнопкой предложит **3 дня бесплатно** — по
кнопке «Получить», а не автоматически. Срок и лимиты меняются в `.env`
(`TRIAL_DAYS`, `TRIAL_DEVICES`), выключается через `TRIAL_ENABLED=false`.

Подробности и грабли — в [bot/README.md](bot/README.md).

---

## Шаг 10. Caddy — собираем `sub` воедино

Теперь на домене подписки живут три вещи. Допишите в
`/opt/remnawave/caddy/Caddyfile`:

```caddyfile
https://sub.example.com {
    encode

    handle /get/* {
        reverse_proxy http://vpn-bot:8080
    }

    handle /cryptobot/webhook {
        reverse_proxy http://vpn-bot:8080
    }

    handle {
        reverse_proxy http://remnawave-subscription-page:3010
    }
}
```

Блоки `https://panel.example.com` и `:443` не трогайте — они от шага 1.

```bash
cd /opt/remnawave/caddy
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

Пропишите вебхук в @CryptoBot → Crypto Pay → ваше приложение → Webhooks:

```
https://sub.example.com/cryptobot/webhook
```

Теперь на `sub` можно включить оранжевый значок Cloudflare. На `panel`
оставьте серый.

### Проверка шага 10

```bash
curl -s https://sub.example.com/healthz                            # {"ok": true}
curl -s -o /dev/null -w '%{http_code}\n' https://sub.example.com   # 200
```

---

## Шаг 11. Лимит устройств

Чтобы одну подписку не расшаривали на весь подъезд:

```bash
cd ~/VPN && ./enable-hwid.sh --limit 3
```

**Включайте только после того, как проверили подключение на живом
пользователе.** После включения клиенты, которые не умеют HWID, перестают
получать подписку вообще. Happ, v2rayNG и Streisand HWID шлют.

Откатить: `./enable-hwid.sh --off`.

---

## Шаг 12. Белые списки (по желанию)

Чтобы через туннель шли только заблокированные сайты, а остальное — напрямую:

```bash
cd ~/VPN/whitelist && ./build-whitelist.sh
```

Файлы из `out/` вставляются в панель → **Templates** → **Subscription
Templates**. Подробности — в [whitelist/README.md](whitelist/README.md).

---

## Шаг 13. Бэкапы

```bash
cp ~/VPN/backup.sh /opt/remnawave/backup.sh
chmod +x /opt/remnawave/backup.sh
nano /opt/remnawave/backup.sh    # вписать TG_TOKEN и TG_CHAT
/opt/remnawave/backup.sh         # прогнать руками, убедиться что дошло
crontab -e
```

```
0 4 * * * /opt/remnawave/backup.sh >> /var/log/rw-backup.log 2>&1
```

И **заберите с сервера `/opt/remnawave/backups/env.backup`** — без `.env` дамп
базы бесполезен, а в Telegram он намеренно не отправляется.

---

## Финальная проверка

```bash
curl -s -o /dev/null -w 'панель      %{http_code}\n' https://panel.example.com
curl -sk -o /dev/null -w 'маскировка %{http_code}\n' https://ВАШ_IP_ПАНЕЛИ
curl -s -o /dev/null -w 'подписка    %{http_code}\n' https://sub.example.com
curl -s https://sub.example.com/healthz
./enable-hwid.sh --status
docker ps --format '{{.Names}}\t{{.Status}}'
```

Ожидаем: панель `200`, маскировка `204`, подписка `200`, бот `{"ok": true}`,
HWID `ВКЛЮЧЕН`, все контейнеры `Up`.

Живая проверка целиком: с другого Telegram-аккаунта купите доступ через бота,
выберите устройство, откройте одноразовую ссылку на телефоне, подключитесь.
Потом откройте ту же ссылку второй раз — должна отдать отказ.

---

## Куда смотреть, когда сломалось

```bash
cd /opt/remnawave       && docker compose logs -f    # панель
cd /opt/remnawave/caddy && docker compose logs -f    # Caddy и сертификаты
cd /opt/remnawave/bot   && docker compose logs -f    # бот
docker logs remnanode -f                             # нода, на её сервере

docker exec -it remnawave remnawave                  # rescue CLI, если заперлись
```

| Симптом | Причина в девяти случаях из десяти |
|---|---|
| Сертификат не выпускается | оранжевый значок Cloudflare или DNS не разошёлся |
| Нода серая | 2222 закрыт для IP панели |
| Подключается, но рвётся | не совпали ключи или SNI между профилем и Host |
| В клиенте пусто | пользователь не в скваде |
| Бот не видит панель | контейнер не в сети `remnawave-network` |
| Подписка перестала выдаваться | включили HWID, а клиент его не шлёт |
