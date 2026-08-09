"""HTTP-часть: выдача одноразовых ссылок и вебхук CryptoBot.

Ставится за Caddy на домене подписки. Наружу торчат два пути:
  GET  {LINK_PATH}/{token}   — страница подключения, срабатывает один раз
  POST /cryptobot/webhook    — уведомление об оплате
"""

import html
import json
import logging
from typing import Awaitable, Callable

from aiohttp import web

from . import devices
from .config import Config
from .cryptobot import CryptoBot, parse_payload
from .db import Db

log = logging.getLogger(__name__)

# Телеграм дёргает ссылку сам, чтобы показать превью. Если не отсечь его,
# одноразовая ссылка сгорит ещё до того, как её откроет человек.
CRAWLER_MARKERS = ('telegrambot', 'twitterbot', 'facebookexternalhit', 'whatsapp',
                   'slackbot', 'discordbot', 'skypeuripreview', 'vkshare')


def _is_crawler(user_agent: str) -> bool:
    ua = (user_agent or '').lower()
    return any(marker in ua for marker in CRAWLER_MARKERS)


def build_app(cfg: Config, db: Db, crypto: CryptoBot,
              on_paid: Callable[[int, str, str], Awaitable[None]]) -> web.Application:
    app = web.Application()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({'ok': True})

    async def one_time_link(request: web.Request) -> web.Response:
        token = request.match_info['token']

        if _is_crawler(request.headers.get('User-Agent', '')):
            # Превью-боту отдаём заглушку и НЕ гасим ссылку
            return web.Response(text=_page_preview(), content_type='text/html', status=200)

        link, problem = await db.burn_link(token)
        if problem:
            return web.Response(text=_page_problem(problem), content_type='text/html', status=410)

        platform = devices.get(link.platform)
        if platform is None:
            return web.Response(text=_page_problem('unknown'),
                                content_type='text/html', status=410)

        return web.Response(
            text=_page_config(platform, link.sub_url, link.username),
            content_type='text/html',
            headers={'Cache-Control': 'no-store, no-cache, must-revalidate'},
        )

    async def cryptobot_webhook(request: web.Request) -> web.Response:
        body = await request.read()
        signature = request.headers.get('crypto-pay-api-signature', '')
        if not crypto.check_signature(body, signature):
            log.warning('Вебхук CryptoBot с неверной подписью, отбрасываю')
            return web.json_response({'ok': False}, status=403)

        try:
            update = json.loads(body)
        except ValueError:
            return web.json_response({'ok': False}, status=400)

        if update.get('update_type') != 'invoice_paid':
            return web.json_response({'ok': True})

        invoice = update.get('payload') or {}
        parsed = parse_payload(invoice.get('payload') or '')
        if not parsed:
            log.warning('Оплаченный счёт без разбираемого payload: %s', invoice.get('invoice_id'))
            return web.json_response({'ok': True})

        tg_id, tariff_id = parsed
        await on_paid(tg_id, tariff_id, str(invoice.get('invoice_id')))
        return web.json_response({'ok': True})

    app.router.add_get('/healthz', health)
    app.router.add_get(cfg.link_path + '/{token}', one_time_link)
    app.router.add_post('/cryptobot/webhook', cryptobot_webhook)
    return app


# ── Страницы ──────────────────────────────────────────────────

_STYLE = """
:root { --bg:#f4f5f7; --card:#fff; --fg:#12141a; --muted:#5c6270;
        --accent:#2f6df6; --accent-fg:#fff; --line:#e3e6ec; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#0f1116; --card:#171a21; --fg:#eceef3; --muted:#9aa1b1;
          --accent:#5b8cff; --accent-fg:#0b0d12; --line:#262b36; }
}
* { box-sizing:border-box; }
body { margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
       font:16px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }
.card { max-width:520px; margin:0 auto; background:var(--card); border:1px solid var(--line);
        border-radius:16px; padding:28px 24px; }
h1 { font-size:22px; margin:0 0 6px; }
p  { color:var(--muted); margin:0 0 18px; }
ol { padding-left:20px; margin:0 0 20px; }
li { margin-bottom:10px; }
.btn { display:block; text-align:center; text-decoration:none; padding:14px 18px;
       border-radius:12px; font-weight:600; margin-bottom:10px; }
.primary { background:var(--accent); color:var(--accent-fg); }
.ghost { border:1px solid var(--line); color:var(--fg); }
.sub { margin-top:22px; padding-top:18px; border-top:1px solid var(--line); }
code { display:block; word-break:break-all; background:var(--bg); border:1px solid var(--line);
       border-radius:10px; padding:12px; font-size:13px; color:var(--fg); }
.warn { color:var(--muted); font-size:14px; margin-top:18px; }
"""


def _shell(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow">'
            f'<title>{html.escape(title)}</title><style>{_STYLE}</style></head>'
            f'<body><div class="card">{body}</div></body></html>')


def _page_config(platform: devices.Platform, sub_url: str, username: str) -> str:
    app = platform.primary
    alt = platform.apps[1] if len(platform.apps) > 1 else None

    blocks = [
        f'<h1>{platform.emoji} Подключение — {html.escape(platform.title)}</h1>',
        '<p>Ссылка одноразовая: страница больше не откроется. '
        'Настройте устройство сейчас.</p>',
        '<ol>',
        f'<li>Установите <b>{html.escape(app.name)}</b>, если его ещё нет.</li>',
        '<li>Нажмите «Добавить подписку» — приложение откроется само.</li>',
        '<li>В приложении нажмите «Подключить».</li>',
        '</ol>',
        f'<a class="btn primary" href="{html.escape(app.link_for(sub_url, username))}">'
        f'Добавить подписку в {html.escape(app.name)}</a>',
        f'<a class="btn ghost" href="{html.escape(app.install_url)}">'
        f'Установить {html.escape(app.name)}</a>',
    ]
    if alt:
        blocks.append(
            f'<div class="sub"><p>Не подошёл {html.escape(app.name)}? Запасной вариант — '
            f'{html.escape(alt.name)}:</p>'
            f'<a class="btn ghost" href="{html.escape(alt.install_url)}">'
            f'Установить {html.escape(alt.name)}</a>'
            f'<a class="btn ghost" href="{html.escape(alt.link_for(sub_url, username))}">'
            f'Добавить подписку в {html.escape(alt.name)}</a></div>')

    blocks.append(
        '<div class="sub"><p>Если кнопки не сработали — добавьте подписку вручную '
        'по этой ссылке:</p>'
        f'<code>{html.escape(sub_url)}</code></div>'
        '<p class="warn">Никому не передавайте эту ссылку: по ней подключаются '
        'к вашей подписке.</p>')
    return _shell('Подключение VPN', ''.join(blocks))


def _page_problem(reason: str) -> str:
    texts = {
        'used': ('Ссылка уже использована',
                 'Каждая ссылка работает один раз. Запросите новую в боте — '
                 'команда /devices.'),
        'expired': ('Срок ссылки истёк',
                    'Ссылка живёт недолго по соображениям безопасности. '
                    'Запросите новую в боте — команда /devices.'),
        'unknown': ('Ссылка не найдена',
                    'Возможно, она набрана с ошибкой. Запросите новую в боте — '
                    'команда /devices.'),
    }
    title, text = texts.get(reason, texts['unknown'])
    return _shell(title, f'<h1>{html.escape(title)}</h1><p>{html.escape(text)}</p>')


def _page_preview() -> str:
    return _shell('Подключение VPN',
                  '<h1>Подключение VPN</h1><p>Откройте ссылку, чтобы настроить устройство.</p>')
