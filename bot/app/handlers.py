"""Диалог бота: тарифы → оплата → выбор устройства → одноразовая ссылка."""

import logging
from datetime import datetime, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           LinkPreviewOptions, Message)

from . import devices
from .config import Config, Tariff
from .cryptobot import CryptoBot, invoice_payload
from .db import Db
from .remnawave import Remnawave

log = logging.getLogger(__name__)
router = Router()

# Заполняется на старте в __main__
cfg: Config
db: Db
panel: Remnawave
crypto: CryptoBot

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def setup(config: Config, database: Db, remnawave: Remnawave, cryptobot: CryptoBot) -> None:
    global cfg, db, panel, crypto
    cfg, db, panel, crypto = config, database, remnawave, cryptobot


# ── Клавиатуры ────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💳 Купить доступ', callback_data='buy')],
        [InlineKeyboardButton(text='📱 Подключить устройство', callback_data='devices')],
        [InlineKeyboardButton(text='ℹ️ Моя подписка', callback_data='status')],
    ])


def tariffs_kb() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text=f'{t.title} — {t.price} {t.asset}',
            callback_data=f'pay:{t.id}')]
        for t in cfg.tariffs
    ]
    rows.append([InlineKeyboardButton(text='‹ Назад', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def devices_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for key in devices.PLATFORM_ORDER:
        p = devices.PLATFORMS[key]
        row.append(InlineKeyboardButton(text=f'{p.emoji} {p.title}',
                                        callback_data=f'dev:{p.key}'))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text='‹ Назад', callback_data='menu')])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Команды ───────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        'Привет! Здесь можно оплатить VPN и подключить к нему устройства.\n\n'
        'После оплаты вы выбираете устройство — iPhone, Android, Windows или Mac — '
        'и получаете одноразовую ссылку, которая сама настроит приложение.',
        reply_markup=main_menu(),
    )


@router.message(Command('devices'))
async def cmd_devices(message: Message) -> None:
    await _ask_device(message.answer, message.from_user.id)


@router.message(Command('status'))
async def cmd_status(message: Message) -> None:
    await message.answer(await _status_text(message.from_user.id),
                         reply_markup=main_menu(), link_preview_options=NO_PREVIEW)


# ── Меню ──────────────────────────────────────────────────────

@router.callback_query(F.data == 'menu')
async def cb_menu(call: CallbackQuery) -> None:
    await call.message.edit_text('Чем помочь?', reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == 'buy')
async def cb_buy(call: CallbackQuery) -> None:
    await call.message.edit_text('Выберите тариф:', reply_markup=tariffs_kb())
    await call.answer()


@router.callback_query(F.data == 'status')
async def cb_status(call: CallbackQuery) -> None:
    await call.message.edit_text(await _status_text(call.from_user.id),
                                 reply_markup=main_menu(),
                                 link_preview_options=NO_PREVIEW)
    await call.answer()


# ── Оплата ────────────────────────────────────────────────────

@router.callback_query(F.data.startswith('pay:'))
async def cb_pay(call: CallbackQuery) -> None:
    tariff = cfg.tariff(call.data.split(':', 1)[1])
    if tariff is None:
        await call.answer('Тариф не найден', show_alert=True)
        return

    try:
        invoice = await crypto.create_invoice(
            amount=tariff.price,
            asset=tariff.asset,
            description=f'VPN — {tariff.title}',
            payload=invoice_payload(call.from_user.id, tariff.id),
        )
    except Exception:
        log.exception('Не смог выставить счёт')
        await call.answer('Платёжный сервис недоступен, попробуйте позже', show_alert=True)
        return

    invoice_id = str(invoice['invoice_id'])
    pay_url = invoice.get('bot_invoice_url') or invoice.get('pay_url')
    await db.add_payment(invoice_id, call.from_user.id, tariff.id)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Оплатить', url=pay_url)],
        [InlineKeyboardButton(text='Я оплатил — проверить',
                              callback_data=f'check:{invoice_id}')],
        [InlineKeyboardButton(text='‹ Назад', callback_data='buy')],
    ])
    await call.message.edit_text(
        f'<b>{tariff.title}</b>\n'
        f'Цена: {tariff.price} {tariff.asset}\n'
        f'Срок: {tariff.days} дн.\n'
        f'Устройств: {tariff.devices}\n\n'
        'Оплатите счёт и вернитесь сюда.',
        reply_markup=kb,
    )
    await call.answer()


@router.callback_query(F.data.startswith('check:'))
async def cb_check(call: CallbackQuery) -> None:
    """Запасной путь на случай, если вебхук не дошёл."""
    invoice_id = call.data.split(':', 1)[1]
    try:
        invoice = await crypto.get_invoice(invoice_id)
    except Exception:
        log.exception('Не смог проверить счёт %s', invoice_id)
        await call.answer('Не получилось проверить, попробуйте ещё раз', show_alert=True)
        return

    if not invoice or invoice.get('status') != 'paid':
        await call.answer('Оплата пока не видна. Если только что заплатили — '
                          'подождите минуту.', show_alert=True)
        return

    payment = await db.get_payment(invoice_id)
    tariff_id = payment['tariff_id'] if payment else None
    if not tariff_id:
        await call.answer('Не нашёл этот счёт', show_alert=True)
        return

    await apply_payment(call.bot, call.from_user.id, tariff_id, invoice_id)
    await call.answer('Оплата принята')


async def apply_payment(bot: Bot, tg_id: int, tariff_id: str, invoice_id: str) -> None:
    """Начисляет доступ. Вызывается и из вебхука, и из кнопки проверки."""
    tariff = cfg.tariff(tariff_id)
    if tariff is None:
        log.error('Оплачен неизвестный тариф %s', tariff_id)
        return

    if not await db.mark_applied(invoice_id):
        log.info('Счёт %s уже начислен, пропускаю', invoice_id)
        return

    try:
        user = await panel.provision(tg_id, tariff.days, tariff.devices, tariff.traffic_bytes)
    except Exception:
        log.exception('Не смог создать пользователя в панели для %s', tg_id)
        await bot.send_message(
            tg_id,
            'Оплата прошла, но панель не ответила. Напишите в поддержку — '
            'доступ выдадим руками, платить второй раз не нужно.')
        return

    await db.save_user(tg_id, user['uuid'], user['username'], user['subscriptionUrl'])
    await bot.send_message(
        tg_id,
        f'✅ Оплата получена, доступ активен до '
        f'<b>{_fmt_date(user.get("expireAt"))}</b>.\n'
        f'Устройств по тарифу: {tariff.devices}.\n\n'
        'Теперь выберите устройство, которое настраиваем:',
        reply_markup=devices_kb(),
    )


# ── Выбор устройства и одноразовая ссылка ─────────────────────

@router.callback_query(F.data == 'devices')
async def cb_devices(call: CallbackQuery) -> None:
    await _ask_device(call.message.edit_text, call.from_user.id)
    await call.answer()


async def _ask_device(send, tg_id: int) -> None:
    user = await db.get_user(tg_id)
    if not user:
        await send('Сначала нужно оплатить доступ.', reply_markup=main_menu())
        return
    await send('Какое устройство настраиваем?', reply_markup=devices_kb())


@router.callback_query(F.data.startswith('dev:'))
async def cb_device(call: CallbackQuery) -> None:
    platform = devices.get(call.data.split(':', 1)[1])
    if platform is None:
        await call.answer('Неизвестное устройство', show_alert=True)
        return

    user = await db.get_user(call.from_user.id)
    if not user:
        await call.answer('Сначала оплатите доступ', show_alert=True)
        return

    token = await db.create_link(call.from_user.id, platform.key, user['sub_url'],
                                 user['username'], cfg.link_ttl_minutes)
    url = cfg.link_url(token)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f'{platform.emoji} Настроить {platform.title}', url=url)],
        [InlineKeyboardButton(text='‹ Другое устройство', callback_data='devices')],
    ])
    await call.message.edit_text(
        f'{platform.emoji} <b>{platform.title}</b>\n\n'
        f'Ссылка одноразовая и живёт {cfg.link_ttl_minutes} минут — '
        'откройте её <b>на том устройстве, которое настраиваете</b>.\n\n'
        'Понадобится ещё одно устройство — вернитесь сюда и возьмите новую ссылку.',
        reply_markup=kb,
        link_preview_options=NO_PREVIEW,
    )
    await call.answer()


# ── Вспомогательное ───────────────────────────────────────────

async def _status_text(tg_id: int) -> str:
    local = await db.get_user(tg_id)
    if not local:
        return 'Подписки пока нет. Нажмите «Купить доступ».'
    try:
        user = await panel.find_by_username(local['username'])
    except Exception:
        log.exception('Панель не ответила про %s', tg_id)
        return 'Панель сейчас не отвечает, попробуйте позже.'
    if not user:
        return 'Подписка не найдена в панели. Напишите в поддержку.'

    active = await db.active_links(tg_id)
    lines = [
        f'Статус: <b>{"активна" if user.get("status") == "ACTIVE" else user.get("status")}</b>',
        f'Действует до: <b>{_fmt_date(user.get("expireAt"))}</b>',
        f'Устройств по тарифу: <b>{user.get("hwidDeviceLimit") or "без лимита"}</b>',
    ]
    if active:
        lines.append(f'Неиспользованных ссылок: <b>{len(active)}</b>')
    return '\n'.join(lines)


def _fmt_date(value) -> str:
    if not value:
        return '—'
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value)
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime('%d.%m.%Y')
