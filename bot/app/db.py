"""Хранилище: SQLite. Здесь же логика одноразовых ссылок.

Ссылка одноразовая в буквальном смысле: первый успешный показ страницы
проставляет used_at, дальше по этому токену отдаётся отказ.
"""

import secrets
import time
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    tg_id       INTEGER PRIMARY KEY,
    remna_uuid  TEXT    NOT NULL,
    username    TEXT    NOT NULL,
    sub_url     TEXT    NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    invoice_id  TEXT    PRIMARY KEY,
    tg_id       INTEGER NOT NULL,
    tariff_id   TEXT    NOT NULL,
    status      TEXT    NOT NULL,           -- pending | paid | applied
    created_at  INTEGER NOT NULL,
    applied_at  INTEGER
);
CREATE INDEX IF NOT EXISTS payments_tg_idx ON payments(tg_id);

CREATE TABLE IF NOT EXISTS links (
    token       TEXT    PRIMARY KEY,
    tg_id       INTEGER NOT NULL,
    platform    TEXT    NOT NULL,
    sub_url     TEXT    NOT NULL,
    username    TEXT    NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    used_at     INTEGER
);
CREATE INDEX IF NOT EXISTS links_tg_idx ON links(tg_id);
"""


@dataclass
class Link:
    token: str
    tg_id: int
    platform: str
    sub_url: str
    username: str
    created_at: int
    expires_at: int
    used_at: int | None


class Db:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        parent = Path(self.path).parent
        if str(parent) not in ('', '.'):
            parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as conn:
            await conn.executescript(SCHEMA)
            await conn.commit()

    # ── Пользователи ─────────────────────────────────────────
    async def save_user(self, tg_id: int, uuid: str, username: str, sub_url: str) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                'INSERT INTO users (tg_id, remna_uuid, username, sub_url, created_at) '
                'VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(tg_id) DO UPDATE SET '
                '  remna_uuid = excluded.remna_uuid, '
                '  username   = excluded.username, '
                '  sub_url    = excluded.sub_url',
                (tg_id, uuid, username, sub_url, int(time.time())),
            )
            await conn.commit()

    async def get_user(self, tg_id: int) -> dict | None:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute('SELECT * FROM users WHERE tg_id = ?', (tg_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    # ── Платежи ──────────────────────────────────────────────
    async def add_payment(self, invoice_id: str, tg_id: int, tariff_id: str) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                'INSERT OR IGNORE INTO payments (invoice_id, tg_id, tariff_id, status, created_at) '
                'VALUES (?, ?, ?, ?, ?)',
                (str(invoice_id), tg_id, tariff_id, 'pending', int(time.time())),
            )
            await conn.commit()

    async def get_payment(self, invoice_id: str) -> dict | None:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                'SELECT * FROM payments WHERE invoice_id = ?', (str(invoice_id),))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def mark_applied(self, invoice_id: str) -> bool:
        """Переводит платёж в applied. False — значит его уже применили.

        Защита от двойного начисления: вебхук CryptoBot и кнопка «я оплатил»
        легко приходят одновременно.
        """
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                "UPDATE payments SET status = 'applied', applied_at = ? "
                "WHERE invoice_id = ? AND status != 'applied'",
                (int(time.time()), str(invoice_id)),
            )
            await conn.commit()
            return cur.rowcount > 0

    # ── Одноразовые ссылки ───────────────────────────────────
    async def create_link(self, tg_id: int, platform: str, sub_url: str,
                          username: str, ttl_minutes: int) -> str:
        token = secrets.token_urlsafe(24)
        now = int(time.time())
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                'INSERT INTO links (token, tg_id, platform, sub_url, username, '
                'created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (token, tg_id, platform, sub_url, username, now, now + ttl_minutes * 60),
            )
            await conn.commit()
        return token

    async def peek_link(self, token: str) -> Link | None:
        """Читает ссылку, не сжигая её."""
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute('SELECT * FROM links WHERE token = ?', (token,))
            row = await cur.fetchone()
            return Link(**dict(row)) if row else None

    async def burn_link(self, token: str) -> tuple[Link | None, str]:
        """Гасит ссылку. Возвращает (ссылка, причина отказа).

        Причина: '' — всё хорошо, 'unknown' | 'expired' | 'used'.
        Гашение атомарное: UPDATE с условием used_at IS NULL не даст двум
        одновременным запросам обоим получить конфиг.
        """
        link = await self.peek_link(token)
        if link is None:
            return None, 'unknown'
        if link.used_at is not None:
            return link, 'used'
        if link.expires_at < int(time.time()):
            return link, 'expired'

        now = int(time.time())
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                'UPDATE links SET used_at = ? WHERE token = ? AND used_at IS NULL',
                (now, token),
            )
            await conn.commit()
            if cur.rowcount == 0:
                return link, 'used'
        link.used_at = now
        return link, ''

    async def active_links(self, tg_id: int) -> list[Link]:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                'SELECT * FROM links WHERE tg_id = ? AND used_at IS NULL AND expires_at > ? '
                'ORDER BY created_at DESC',
                (tg_id, int(time.time())),
            )
            return [Link(**dict(r)) for r in await cur.fetchall()]
