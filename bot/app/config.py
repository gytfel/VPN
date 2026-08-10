"""Настройки бота. Всё читается из окружения, секретов в коде нет."""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Tariff:
    id: str
    title: str
    days: int
    devices: int
    price: str
    asset: str
    traffic_gb: int = 0          # 0 — безлимит

    @property
    def traffic_bytes(self) -> int:
        return self.traffic_gb * 1024 ** 3


def _req(name: str) -> str:
    val = os.getenv(name, '').strip()
    if not val:
        raise SystemExit(f'Не задана переменная окружения {name} — смотрите .env.example')
    return val


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, '').strip()
    return int(raw) if raw else default


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]

    remnawave_url: str
    remnawave_token: str
    remnawave_squad_uuid: str | None

    public_base: str             # https://sub.example.com
    link_path: str               # /get
    link_ttl_minutes: int

    cryptobot_token: str
    cryptobot_api: str

    db_path: str
    web_host: str
    web_port: int

    trial_enabled: bool
    trial_days: int
    trial_devices: int
    trial_traffic_gb: int

    tariffs: tuple[Tariff, ...]

    @property
    def trial(self) -> Tariff:
        """Пробный период как обычный тариф — чтобы выдавать его тем же кодом."""
        return Tariff(
            id='trial', title=f'Пробные {self.trial_days} дня',
            days=self.trial_days, devices=self.trial_devices,
            price='0', asset='', traffic_gb=self.trial_traffic_gb,
        )

    def tariff(self, tariff_id: str) -> Tariff | None:
        return next((t for t in self.tariffs if t.id == tariff_id), None)

    def link_url(self, token: str) -> str:
        return f"{self.public_base.rstrip('/')}{self.link_path}/{token}"


def load(tariffs_file: str | None = None) -> Config:
    path = Path(tariffs_file or os.getenv('TARIFFS_FILE', 'tariffs.json'))
    if not path.exists():
        raise SystemExit(f'Не нашёл файл тарифов: {path}')
    raw = json.loads(path.read_text(encoding='utf-8'))
    tariffs = tuple(Tariff(**t) for t in raw)
    if not tariffs:
        raise SystemExit('Список тарифов пуст')

    admins = os.getenv('ADMIN_IDS', '').replace(',', ' ').split()
    testnet = os.getenv('CRYPTOBOT_TESTNET', 'false').lower() == 'true'

    return Config(
        bot_token=_req('BOT_TOKEN'),
        admin_ids=frozenset(int(a) for a in admins),
        remnawave_url=os.getenv('REMNAWAVE_URL', 'http://remnawave:3000').rstrip('/'),
        remnawave_token=_req('REMNAWAVE_TOKEN'),
        remnawave_squad_uuid=os.getenv('REMNAWAVE_SQUAD_UUID', '').strip() or None,
        public_base=_req('PUBLIC_BASE'),
        link_path='/' + os.getenv('LINK_PATH', 'get').strip('/'),
        link_ttl_minutes=_int('LINK_TTL_MINUTES', 30),
        cryptobot_token=_req('CRYPTOBOT_TOKEN'),
        cryptobot_api=('https://testnet-pay.crypt.bot/api' if testnet
                       else 'https://pay.crypt.bot/api'),
        db_path=os.getenv('DB_PATH', 'data/bot.sqlite3'),
        web_host=os.getenv('WEB_HOST', '0.0.0.0'),
        web_port=_int('WEB_PORT', 8080),
        trial_enabled=os.getenv('TRIAL_ENABLED', 'true').lower() == 'true',
        trial_days=_int('TRIAL_DAYS', 3),
        trial_devices=_int('TRIAL_DEVICES', 1),
        trial_traffic_gb=_int('TRIAL_TRAFFIC_GB', 0),
        tariffs=tariffs,
    )
