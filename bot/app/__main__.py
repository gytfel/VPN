"""Точка входа: поднимает бота и HTTP-сервер в одном процессе."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from . import config, handlers
from .cryptobot import CryptoBot
from .db import Db
from .remnawave import Remnawave
from .web import build_app

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
)
log = logging.getLogger('bot')


async def main() -> None:
    cfg = config.load()

    db = Db(cfg.db_path)
    await db.init()

    panel = Remnawave(cfg.remnawave_url, cfg.remnawave_token, cfg.remnawave_squad_uuid)
    crypto = CryptoBot(cfg.cryptobot_token, cfg.cryptobot_api)

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    handlers.setup(cfg, db, panel, crypto)
    dp.include_router(handlers.router)

    async def on_paid(tg_id: int, tariff_id: str, invoice_id: str) -> None:
        await handlers.apply_payment(bot, tg_id, tariff_id, invoice_id)

    http = build_app(cfg, db, crypto, on_paid)
    runner = web.AppRunner(http)
    await runner.setup()
    site = web.TCPSite(runner, cfg.web_host, cfg.web_port)
    await site.start()
    log.info('HTTP слушает %s:%s, ссылки на %s%s/…',
             cfg.web_host, cfg.web_port, cfg.public_base, cfg.link_path)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
