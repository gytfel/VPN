"""Проверка боевой логики без Telegram и без сети.

Запуск:
    cd bot && python -m tests.smoke
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from app import devices  # noqa: E402
from app.config import Config, Tariff  # noqa: E402
from app.cryptobot import CryptoBot, invoice_payload, parse_payload  # noqa: E402
from app.db import Db  # noqa: E402
from app.web import build_app  # noqa: E402

# Часть проверок намеренно ломает панель — их traceback в выводе не нужен
logging.disable(logging.CRITICAL)

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = '') -> None:
    print(('  [ok]   ' if ok else '  [FAIL] ') + name + (f'  — {detail}' if detail and not ok else ''))
    if not ok:
        FAILS.append(name)


SUB_URL = 'https://sub.example.com/abcdef123456'
USERNAME = 'tg123456'


def make_cfg(db_path: str, trial_enabled: bool = True) -> Config:
    return Config(
        bot_token='x', admin_ids=frozenset(),
        remnawave_url='http://remnawave:3000', remnawave_token='t',
        remnawave_squad_uuid=None,
        public_base='https://sub.example.com', link_path='/get', link_ttl_minutes=30,
        cryptobot_token='cbtoken', cryptobot_api='https://pay.crypt.bot/api',
        db_path=db_path, web_host='127.0.0.1', web_port=0,
        trial_enabled=trial_enabled, trial_days=3, trial_devices=1, trial_traffic_gb=0,
        tariffs=(Tariff('m1', '1 месяц', 30, 1, '1.5', 'USDT'),),
    )


async def test_deep_links() -> None:
    print('\nDeep-link\'и по платформам')
    expected = {
        'ios': f'happ://add/{SUB_URL}',
        'android': f'happ://add/{SUB_URL}',
        'windows': f'happ://add/{SUB_URL}',
        'macos': f'happ://add/{SUB_URL}',
    }
    for key, want in expected.items():
        plat = devices.get(key)
        check(f'{key}: основное приложение Happ', plat.primary.name == 'Happ')
        check(f'{key}: ссылка {want[:24]}…', plat.primary.link_for(SUB_URL, USERNAME) == want)

    # у платформ с {user} подставляется имя
    v2ray = devices.get('android').apps[1]
    link = v2ray.link_for(SUB_URL, USERNAME)
    check('android: запасной v2rayNG подставляет имя и url',
          link == f'v2rayng://install-config?name={USERNAME}&url={SUB_URL}', link)

    check('ровно 4 платформы', len(devices.PLATFORM_ORDER) == 4)


async def test_one_time_link() -> None:
    print('\nОдноразовость ссылки')
    with tempfile.TemporaryDirectory() as tmp:
        db = Db(os.path.join(tmp, 'b.sqlite3'))
        await db.init()

        token = await db.create_link(1, 'ios', SUB_URL, USERNAME, 30)
        link, problem = await db.burn_link(token)
        check('первое использование проходит', problem == '' and link is not None, problem)

        _, problem = await db.burn_link(token)
        check('второе использование отбивается', problem == 'used', problem)

        _, problem = await db.burn_link('нет-такого-токена')
        check('неизвестный токен отбивается', problem == 'unknown', problem)

        # протухшая
        expired = await db.create_link(1, 'ios', SUB_URL, USERNAME, 30)
        import aiosqlite
        async with aiosqlite.connect(db.path) as conn:
            await conn.execute('UPDATE links SET expires_at = ? WHERE token = ?',
                               (int(time.time()) - 1, expired))
            await conn.commit()
        _, problem = await db.burn_link(expired)
        check('протухшая ссылка отбивается', problem == 'expired', problem)

        # гонка: два одновременных запроса — конфиг получает только один
        race = await db.create_link(1, 'android', SUB_URL, USERNAME, 30)
        results = await asyncio.gather(db.burn_link(race), db.burn_link(race))
        winners = [p for _, p in results if p == '']
        check('при гонке выигрывает ровно один', len(winners) == 1, str(results))


async def test_web() -> None:
    print('\nHTTP-выдача')
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(os.path.join(tmp, 'b.sqlite3'))
        db = Db(cfg.db_path)
        await db.init()
        crypto = CryptoBot(cfg.cryptobot_token, cfg.cryptobot_api)

        paid: list[tuple] = []

        async def on_paid(tg_id, tariff_id, invoice_id):
            paid.append((tg_id, tariff_id, invoice_id))

        app = build_app(cfg, db, crypto, on_paid)
        async with TestClient(TestServer(app)) as client:
            token = await db.create_link(42, 'ios', SUB_URL, USERNAME, 30)

            # превью-бот Телеграма НЕ должен сжигать ссылку
            resp = await client.get(f'/get/{token}', headers={'User-Agent': 'TelegramBot (like TwitterBot)'})
            check('превью-бот получает заглушку', resp.status == 200)
            still = await db.peek_link(token)
            check('после превью-бота ссылка цела', still.used_at is None)

            resp = await client.get(f'/get/{token}', headers={'User-Agent': 'Mozilla/5.0 iPhone'})
            body = await resp.text()
            check('человек получает страницу', resp.status == 200)
            check('на странице есть deep-link', f'happ://add/{SUB_URL}' in body)
            check('на странице есть ссылка подписки', SUB_URL in body)

            resp = await client.get(f'/get/{token}', headers={'User-Agent': 'Mozilla/5.0 iPhone'})
            check('повторный заход отбивается (410)', resp.status == 410)
            check('и объясняет причину', 'уже использована' in await resp.text())

            resp = await client.get('/get/мусор', headers={'User-Agent': 'Mozilla/5.0'})
            check('несуществующий токен -> 410', resp.status == 410)

            resp = await client.get('/healthz')
            check('healthz жив', resp.status == 200)

            # вебхук: без подписи не проходит
            body = json.dumps({'update_type': 'invoice_paid', 'payload': {
                'invoice_id': 777, 'payload': invoice_payload(42, 'm1')}}).encode()
            resp = await client.post('/cryptobot/webhook', data=body)
            check('вебхук без подписи отбивается (403)', resp.status == 403)
            check('и оплату не начисляет', not paid)

            secret = hashlib.sha256(cfg.cryptobot_token.encode()).digest()
            sig = hmac.new(secret, body, hashlib.sha256).hexdigest()
            resp = await client.post('/cryptobot/webhook', data=body,
                                     headers={'crypto-pay-api-signature': sig})
            check('вебхук с верной подписью принимается', resp.status == 200)
            check('оплата дошла до начисления', paid == [(42, 'm1', '777')], str(paid))


async def test_payment_guard() -> None:
    print('\nЗащита от двойного начисления')
    with tempfile.TemporaryDirectory() as tmp:
        db = Db(os.path.join(tmp, 'b.sqlite3'))
        await db.init()
        await db.add_payment('inv1', 5, 'm1')
        first = await db.mark_applied('inv1')
        second = await db.mark_applied('inv1')
        check('первый раз начисляем', first)
        check('второй раз — нет', not second)

        parsed = parse_payload(invoice_payload(7, 'm3'))
        check('payload счёта разбирается обратно', parsed == (7, 'm3'), str(parsed))
        check('битый payload не роняет', parse_payload('{}') is None)


async def test_remnawave() -> None:
    """Клиент панели против поддельного API: проверяем, что и куда он шлёт."""
    print('\nКлиент Remnawave')
    from aiohttp import web as aweb

    from app.remnawave import Remnawave

    seen: list[tuple[str, str, dict]] = []
    existing: dict = {}

    async def by_username(request):
        name = request.match_info['name']
        seen.append(('GET', f'/by-username/{name}', {}))
        if not existing:
            return aweb.json_response({'message': 'not found'}, status=404)
        return aweb.json_response({'response': existing})

    async def create(request):
        body = await request.json()
        seen.append(('POST', '/api/users', body))
        return aweb.json_response({'response': {
            'uuid': 'u-1', 'username': body['username'], 'shortUuid': 's1',
            'subscriptionUrl': SUB_URL, 'expireAt': body['expireAt'],
            'hwidDeviceLimit': body.get('hwidDeviceLimit'), 'status': 'ACTIVE'}})

    async def update(request):
        body = await request.json()
        seen.append(('PATCH', '/api/users', body))
        return aweb.json_response({'response': {
            'uuid': body['uuid'], 'username': 'tg777', 'shortUuid': 's1',
            'subscriptionUrl': SUB_URL, 'expireAt': body['expireAt'],
            'hwidDeviceLimit': body.get('hwidDeviceLimit'), 'status': 'ACTIVE'}})

    app = aweb.Application()
    app.router.add_get('/api/users/by-username/{name}', by_username)
    app.router.add_post('/api/users', create)
    app.router.add_patch('/api/users', update)

    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url('')).rstrip('/')
    try:
        panel = Remnawave(base, 'token', squad_uuid='squad-uuid-1')

        user = await panel.provision(777, days=30, devices=2)
        created = next(b for m, _, b in seen if m == 'POST' for b in [b])
        check('нового пользователя создаём через POST',
              any(m == 'POST' for m, _, _ in seen))
        check('username из telegram id', created['username'] == 'tg777', created['username'])
        check('telegramId проставлен', created['telegramId'] == 777)
        check('лимит устройств = тарифу', created['hwidDeviceLimit'] == 2)
        check('squad подставлен', created['activeInternalSquads'] == ['squad-uuid-1'])
        check('expireAt в формате ISO с Z', created['expireAt'].endswith('Z'), created['expireAt'])
        check('вернулась ссылка подписки', user['subscriptionUrl'] == SUB_URL)

        # теперь пользователь существует и подписка ещё жива — продление от её конца
        from datetime import datetime, timedelta, timezone
        alive_until = datetime.now(timezone.utc) + timedelta(days=10)
        existing.update({'uuid': 'u-1', 'username': 'tg777',
                         'expireAt': alive_until.isoformat().replace('+00:00', 'Z'),
                         'hwidDeviceLimit': 3, 'subscriptionUrl': SUB_URL})
        seen.clear()
        await panel.provision(777, days=30, devices=1)
        patched = next(b for m, _, b in seen if m == 'PATCH')
        check('существующего продлеваем через PATCH',
              any(m == 'PATCH' for m, _, _ in seen))
        new_expire = datetime.fromisoformat(patched['expireAt'].replace('Z', '+00:00'))
        check('продлили от конца текущей подписки, а не от сегодня',
              39 <= (new_expire - datetime.now(timezone.utc)).days <= 40,
              str((new_expire - datetime.now(timezone.utc)).days))
        check('лимит устройств не понижается',
              patched['hwidDeviceLimit'] == 3, str(patched['hwidDeviceLimit']))

        # просроченная подписка — считаем от сегодня
        expired_at = datetime.now(timezone.utc) - timedelta(days=5)
        existing['expireAt'] = expired_at.isoformat().replace('+00:00', 'Z')
        seen.clear()
        await panel.provision(777, days=30, devices=1)
        patched = next(b for m, _, b in seen if m == 'PATCH')
        new_expire = datetime.fromisoformat(patched['expireAt'].replace('Z', '+00:00'))
        check('просроченную считаем от сегодня',
              29 <= (new_expire - datetime.now(timezone.utc)).days <= 30,
              str((new_expire - datetime.now(timezone.utc)).days))
    finally:
        await server.close()


async def test_trial() -> None:
    """Пробный период: только новым и ровно один раз."""
    print('\nПробный период')
    with tempfile.TemporaryDirectory() as tmp:
        db = Db(os.path.join(tmp, 'b.sqlite3'))
        await db.init()

        check('новый пользователь триал ещё не брал', not await db.trial_taken(1))
        check('первый claim проходит', await db.claim_trial(1))
        check('второй claim не проходит', not await db.claim_trial(1))
        check('после claim триал помечен взятым', await db.trial_taken(1))

        # два одновременных нажатия кнопки
        results = await asyncio.gather(db.claim_trial(2), db.claim_trial(2))
        check('при гонке триал достаётся одному', sum(results) == 1, str(results))

        # если панель не ответила, право вернулось
        await db.release_trial(2)
        check('release возвращает право на триал', not await db.trial_taken(2))
        check('и его можно взять снова', await db.claim_trial(2))

        # доступность кнопки
        from app import handlers
        cfg = make_cfg(db.path)
        handlers.setup(cfg, db, None, None)

        check('триал доступен незнакомцу', await handlers.trial_available(999))
        check('недоступен тому, кто уже брал', not await handlers.trial_available(1))

        await db.save_user(500, 'u-500', 'tg500', SUB_URL)
        check('недоступен тому, у кого есть подписка',
              not await handlers.trial_available(500))

        off = make_cfg(db.path, trial_enabled=False)
        handlers.setup(off, db, None, None)
        check('недоступен при TRIAL_ENABLED=false', not await handlers.trial_available(999))
        handlers.setup(cfg, db, None, None)

        # кнопка в меню
        kb = await handlers.menu_for(999)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        check('кнопка «Получить» есть у новичка',
              any('Получить' in t for t in texts), str(texts))
        check('она первая в меню', 'Получить' in texts[0], str(texts))
        check('её callback — trial', datas[0] == 'trial', str(datas))
        check('в кнопке указан срок', '3 дня' in texts[0], texts[0])

        kb = await handlers.menu_for(1)
        texts = [b.text for row in kb.inline_keyboard for b in row]
        check('у бравшего триал кнопки нет',
              not any('Получить' in t for t in texts), str(texts))

        check('пробный тариф — 3 дня', cfg.trial.days == 3)
        check('пробный тариф бесплатный', cfg.trial.price == '0')


async def test_trial_grant() -> None:
    """Выдача триала целиком: панель, сохранение, сообщение с выбором устройства."""
    print('\nВыдача пробного периода')
    from aiohttp import web as aweb

    from app import handlers
    from app.remnawave import Remnawave

    async def create(request):
        body = await request.json()
        return aweb.json_response({'response': {
            'uuid': 'u-trial', 'username': body['username'], 'shortUuid': 's1',
            'subscriptionUrl': SUB_URL, 'expireAt': body['expireAt'],
            'hwidDeviceLimit': body.get('hwidDeviceLimit'), 'status': 'ACTIVE'}})

    async def not_found(request):
        return aweb.json_response({'message': 'not found'}, status=404)

    app = aweb.Application()
    app.router.add_get('/api/users/by-username/{name}', not_found)
    app.router.add_post('/api/users', create)
    server = TestServer(app)
    await server.start_server()

    class FakeBot:
        def __init__(self):
            self.sent = []

        async def send_message(self, chat_id, text, **kw):
            self.sent.append((chat_id, text, kw))

    try:
        with tempfile.TemporaryDirectory() as tmp:
            db = Db(os.path.join(tmp, 'b.sqlite3'))
            await db.init()
            cfg = make_cfg(db.path)
            panel = Remnawave(str(server.make_url('')).rstrip('/'), 'token')
            handlers.setup(cfg, db, panel, None)

            bot = FakeBot()
            ok = await handlers.grant(bot, 777, cfg.trial, '🎁 Пробный период активирован')
            check('выдача прошла', ok)

            saved = await db.get_user(777)
            check('пользователь сохранён локально', saved and saved['sub_url'] == SUB_URL)

            _, text, kw = bot.sent[0]
            check('в сообщении сказано про пробный период',
                  'Пробный период активирован' in text, text[:60])
            check('предложен выбор устройства', 'выберите устройство' in text.lower())
            kb = kw['reply_markup']
            datas = [b.callback_data for row in kb.inline_keyboard for b in row]
            check('клавиатура — четыре устройства',
                  sum(1 for d in datas if d and d.startswith('dev:')) == 4, str(datas))

            # после выдачи триал больше не предлагается
            await db.claim_trial(777)
            check('кнопка «Получить» пропала', not await handlers.trial_available(777))

            # панель недоступна — право на триал возвращается
            dead = Remnawave('http://127.0.0.1:9', 'token')
            handlers.setup(cfg, db, dead, None)
            bot2 = FakeBot()
            await db.claim_trial(888)
            ok = await handlers.grant(bot2, 888, cfg.trial, '🎁 Пробный период активирован')
            check('при отказе панели выдача возвращает False', not ok)
            check('человеку сказали про поддержку',
                  'поддержку' in bot2.sent[0][1], bot2.sent[0][1][:60])
            await db.release_trial(888)
            check('право на триал вернулось', await handlers.trial_available(888))
    finally:
        await server.close()


async def test_imports() -> None:
    print('\nСборка модулей')
    from app import handlers
    check('хендлеры импортируются', handlers.router is not None)
    check('меню без триала — 3 кнопки', len(handlers.main_menu().inline_keyboard) == 3)
    check('меню с триалом — 4 кнопки', len(handlers.main_menu(True).inline_keyboard) == 4)


async def main() -> None:
    await test_deep_links()
    await test_one_time_link()
    await test_web()
    await test_payment_guard()
    await test_remnawave()
    await test_trial()
    await test_trial_grant()
    await test_imports()
    print()
    if FAILS:
        print(f'ПРОВАЛЕНО: {len(FAILS)} — ' + ', '.join(FAILS))
        sys.exit(1)
    print('Все проверки прошли.')


if __name__ == '__main__':
    asyncio.run(main())
