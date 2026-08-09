# Шаг 1 — сервер панели

Установка Remnawave Panel за Caddy с закрытой по IP админкой.

## До запуска

**VPS:** 2 ядра / 4 ГБ / 20 ГБ, Ubuntu 24.04. Трафик пользователей через панель не идёт — берите дешёвую и стабильную, география не важна.

**Домен.** Обязателен: Remnawave не работает на подпути вроде `/remnawave`, только на корне домена или поддомена.

Нужны два поддомена:

| Поддомен | Для чего | Доступ |
|---|---|---|
| `panel.example.com` | админка | закрыта по IP |
| `sub.example.com` | страница подписки | публичная |

Если посадить подписку на домен панели, закрыть админку по IP уже не выйдет — отрежете клиентов вместе с собой. Поэтому разделяем сразу.

## DNS

A-записи `panel` и `sub` → IP сервера панели.

**Серый значок (DNS only) на время установки.** Caddy должен выпустить сертификат напрямую. После установки на `sub` можно включить оранжевый, на `panel` — см. раздел про Cloudflare ниже.

## Установка

```bash
chmod +x install-panel.sh
sudo ./install-panel.sh
```

Скрипт спросит домены и разрешённые IP, дальше сделает всё сам: фаервол, Docker, панель, секреты, Caddy.

Без вопросов:

```bash
sudo PANEL_DOMAIN=panel.example.com \
     SUB_DOMAIN=sub.example.com \
     ADMIN_IPS="1.2.3.4" \
     ./install-panel.sh
```

`ADMIN_IPS` принимает несколько адресов через пробел и CIDR: `"1.2.3.4 5.6.7.0/24"`.

## Проверка

1. Откройте `https://panel.example.com` — форма регистрации.
2. **Регистрируйтесь немедленно.** Первый зарегистрировавшийся становится super-admin. Пока вы этого не сделали, им может стать кто угодно, кто найдёт домен.
3. Проверьте версию в интерфейсе: нужна **2.7.5 или выше**. В 2.7.4 и раньше была гонка в регистрации HWID, позволявшая обойти лимит устройств и перепродавать одну подписку.
4. Зайдите на `https://IP-сервера` напрямую — должна отдаться пустота. Значит маскировка работает.

## Грабли

### Cloudflare и IP-фильтр

При оранжевом значке Caddy видит IP-адреса Cloudflare, а не ваш. Фильтр `remote_ip` тогда либо заблокирует вас, либо пропустит всех.

Варианты:

- **Просто оставьте на `panel` серый значок.** Фильтр по IP уже прячет панель лучше, чем CF.
- Или включайте оранжевый и меняйте в `Caddyfile` `remote_ip` на `client_ip`, добавив `trusted_proxies` с диапазонами Cloudflare.

На `sub.example.com` оранжевый значок ставьте смело — там фильтра нет.

### Динамический IP дома

Тогда не используйте IP-фильтр (оставьте `ADMIN_IPS` пустым) — иначе будете запираться каждые пару дней. Вместо этого включите **Passkey** или Telegram OAuth в Remnawave → Settings. Голый логин-пароль наружу не оставляйте.

### Заперлись снаружи

```bash
docker exec -it remnawave remnawave
```

Rescue CLI работает с самого сервера, домен и фаервол ему не нужны.

### Сертификат не выпускается

```bash
cd /opt/remnawave/caddy && docker compose logs -f
```

Почти всегда одно из двух: оранжевый значок Cloudflare, либо DNS ещё не разошёлся. Проверьте `getent hosts panel.example.com`.

## Бэкапы

Настройте сразу, пока база пустая и не жалко экспериментировать:

```bash
cp backup.sh /opt/remnawave/backup.sh
chmod +x /opt/remnawave/backup.sh
nano /opt/remnawave/backup.sh    # вписать TG_TOKEN и TG_CHAT
crontab -e
```

Строка в cron:

```
0 4 * * * /opt/remnawave/backup.sh >> /var/log/rw-backup.log 2>&1
```

Дамп уедет вам в Telegram. Проверьте, что первый бэкап дошёл — запустите скрипт руками.

## Что дальше

| Шаг | Что делаем |
|---|---|
| 2 | Нода в первой локации, NODE_PORT закрыт на всё кроме IP панели |
| 3 | Config Profile с VLESS Reality, Host |
| 4 | Остальные локации + Internal Squads под тарифы |
| 5 | Страница подписки на `sub`, шаблон под Happ |
| 6 | HWID Device Limit, плагины ноды (Torrent Blocker, Egress Filter) |
| 7 | Telegram-бот с CryptoBot |

## Файлы после установки

```
/opt/remnawave/
├── docker-compose.yml     панель + Postgres + Valkey
├── .env                   секреты, права 600
├── backup.sh              если скопировали
├── backups/               дампы базы
└── caddy/
    ├── Caddyfile          реверс-прокси и IP-фильтр
    └── docker-compose.yml
```

Полезное:

```bash
cd /opt/remnawave && docker compose logs -f      # логи панели
cd /opt/remnawave/caddy && docker compose logs -f # логи Caddy
docker exec -it remnawave remnawave               # rescue CLI
```
