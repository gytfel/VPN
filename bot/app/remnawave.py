"""Клиент API Remnawave — ровно те вызовы, что нужны боту.

Эндпоинты и поля сверены с libs/contract панели:
  POST   /api/users                — создать
  PATCH  /api/users                — обновить (uuid + поля)
  GET    /api/users/by-username/…  — найти по имени
"""

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

log = logging.getLogger(__name__)


class RemnawaveError(RuntimeError):
    pass


class Remnawave:
    def __init__(self, base_url: str, token: str, squad_uuid: str | None = None):
        self.base = base_url.rstrip('/')
        self.squad_uuid = squad_uuid
        self._headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }

    async def _call(self, method: str, path: str, payload: dict | None = None,
                    allow_404: bool = False) -> dict | None:
        url = f'{self.base}{path}'
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.request(method, url, json=payload, headers=self._headers) as resp:
                text = await resp.text()
                if resp.status == 404 and allow_404:
                    return None
                if resp.status >= 400:
                    log.error('Remnawave %s %s -> %s: %s', method, path, resp.status, text[:400])
                    raise RemnawaveError(f'{method} {path} вернул {resp.status}')
                if not text:
                    return None
                return (await resp.json()).get('response')

    @staticmethod
    def username_for(tg_id: int) -> str:
        # Панель требует ^[a-zA-Z0-9_-]+$ длиной 3–36
        return f'tg{tg_id}'

    async def find_by_username(self, username: str) -> dict | None:
        return await self._call('GET', f'/api/users/by-username/{username}', allow_404=True)

    async def create(self, tg_id: int, days: int, devices: int,
                     traffic_bytes: int = 0) -> dict:
        expire = datetime.now(timezone.utc) + timedelta(days=days)
        payload: dict = {
            'username': self.username_for(tg_id),
            'telegramId': tg_id,
            'status': 'ACTIVE',
            'expireAt': expire.isoformat().replace('+00:00', 'Z'),
            'hwidDeviceLimit': devices,
            'trafficLimitBytes': traffic_bytes,
            'trafficLimitStrategy': 'MONTH' if traffic_bytes else 'NO_RESET',
        }
        if self.squad_uuid:
            payload['activeInternalSquads'] = [self.squad_uuid]
        user = await self._call('POST', '/api/users', payload)
        if not user:
            raise RemnawaveError('Панель не вернула созданного пользователя')
        return user

    async def extend(self, user: dict, days: int, devices: int,
                     traffic_bytes: int = 0) -> dict:
        """Продлевает подписку. Если она ещё жива — от её конца, иначе от сейчас."""
        now = datetime.now(timezone.utc)
        current = _parse_dt(user.get('expireAt'))
        base = current if current and current > now else now
        payload: dict = {
            'uuid': user['uuid'],
            'status': 'ACTIVE',
            'expireAt': (base + timedelta(days=days)).isoformat().replace('+00:00', 'Z'),
            'hwidDeviceLimit': max(devices, user.get('hwidDeviceLimit') or 0),
            'trafficLimitBytes': traffic_bytes,
            'trafficLimitStrategy': 'MONTH' if traffic_bytes else 'NO_RESET',
        }
        if self.squad_uuid:
            payload['activeInternalSquads'] = [self.squad_uuid]
        updated = await self._call('PATCH', '/api/users', payload)
        if not updated:
            raise RemnawaveError('Панель не вернула обновлённого пользователя')
        return updated

    async def provision(self, tg_id: int, days: int, devices: int,
                        traffic_bytes: int = 0) -> dict:
        """Создаёт или продлевает — как получится."""
        existing = await self.find_by_username(self.username_for(tg_id))
        if existing:
            return await self.extend(existing, days, devices, traffic_bytes)
        return await self.create(tg_id, days, devices, traffic_bytes)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
