"""Приём оплаты через CryptoBot (@CryptoBot, Crypto Pay API).

Платёжный слой намеренно отделён от остального: чтобы добавить другой
способ оплаты, достаточно повторить эти три метода.
"""

import hashlib
import hmac
import json
import logging

import aiohttp

log = logging.getLogger(__name__)


class CryptoBotError(RuntimeError):
    pass


class CryptoBot:
    def __init__(self, token: str, api_url: str):
        self.token = token
        self.api = api_url.rstrip('/')

    async def _call(self, method: str, payload: dict) -> dict:
        timeout = aiohttp.ClientTimeout(total=20)
        headers = {'Crypto-Pay-API-Token': self.token}
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.post(f'{self.api}/{method}', json=payload, headers=headers) as resp:
                data = await resp.json()
        if not data.get('ok'):
            log.error('CryptoBot %s -> %s', method, data)
            raise CryptoBotError(f'CryptoBot вернул ошибку на {method}')
        return data['result']

    async def create_invoice(self, amount: str, asset: str, description: str,
                             payload: str) -> dict:
        return await self._call('createInvoice', {
            'amount': str(amount),
            'asset': asset,
            'description': description[:1024],
            'payload': payload,
            'allow_comments': False,
            'allow_anonymous': False,
        })

    async def get_invoice(self, invoice_id: str) -> dict | None:
        result = await self._call('getInvoices', {'invoice_ids': str(invoice_id)})
        items = result.get('items') or []
        return items[0] if items else None

    def check_signature(self, body: bytes, signature: str) -> bool:
        """Подпись вебхука: HMAC-SHA256 с ключом SHA256(токен)."""
        if not signature:
            return False
        secret = hashlib.sha256(self.token.encode()).digest()
        expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def invoice_payload(tg_id: int, tariff_id: str) -> str:
    return json.dumps({'tg_id': tg_id, 'tariff': tariff_id}, separators=(',', ':'))


def parse_payload(raw: str) -> tuple[int, str] | None:
    try:
        data = json.loads(raw)
        return int(data['tg_id']), str(data['tariff'])
    except (ValueError, KeyError, TypeError):
        return None
