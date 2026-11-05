"""Signed server-to-server requests; no Supabase or admin password on Railway."""
import asyncio
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode, quote

import aiohttp
from yarl import URL


class WolfmodError(Exception):
    def __init__(self, status: int, code: str = "backend_unavailable"):
        self.status = status
        self.code = code
        super().__init__(f"Wolfmod API error ({status})")


def sign_request(secret: str, method: str, path: str, timestamp: str, raw: str) -> str:
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    payload = "\n".join((timestamp, method.upper(), path, digest))
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


class WolfmodAPI:
    def __init__(self, base_url: str, secret: str, sku: str = "gemini_plus"):
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.sku = sku
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        return self

    async def __aexit__(self, *_):
        if self.session:
            await self.session.close()

    async def request(self, method: str, path: str, body=None, params=None):
        if not path.startswith("/api/bot/") or "?" in path or "#" in path:
            raise ValueError("Only signed bot routes are supported")
        if not self.session:
            raise RuntimeError("Use WolfmodAPI as an async context manager")
        raw = "" if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(2):
            query = dict(params or {})
            query["nonce"] = secrets.token_hex(16)
            signed_path = path + "?" + urlencode(query)
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "X-Bot-Timestamp": timestamp,
                "X-Bot-Signature": sign_request(self.secret, method, signed_path, timestamp, raw),
            }
            try:
                async with self.session.request(
                    method, URL(self.base_url + signed_path, encoded=True),
                    data=raw.encode("utf-8") if body is not None else None,
                    headers=headers, allow_redirects=False,
                ) as response:
                    if response.status == 204:
                        return {}
                    try:
                        data = await response.json(content_type=None)
                    except (ValueError, UnicodeDecodeError):
                        data = {}
                    if not 200 <= response.status < 300:
                        if response.status >= 500 and attempt == 0:
                            await asyncio.sleep(0.5)
                            continue
                        code = data.get("error", "backend_unavailable") if isinstance(data, dict) else "backend_unavailable"
                        raise WolfmodError(response.status, str(code))
                    if not isinstance(data, dict):
                        raise WolfmodError(502)
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt:
                    raise WolfmodError(503) from None
                await asyncio.sleep(0.5)
        raise WolfmodError(503)

    async def product(self):
        data = await self.request("GET", "/api/bot/products")
        for product in data.get("products", []):
            if product.get("sku") == self.sku:
                if product.get("type") != "item":
                    raise WolfmodError(409, "unsupported_product_type")
                return product
        raise WolfmodError(404, "product_not_found")

    async def inventory(self):
        return await self.request("GET", "/api/bot/inventory", params={"sku": self.sku})

    async def create_order(self, user_id: int, username: str, request_id: str):
        return await self.request("POST", "/api/bot/orders", {
            "client_request_id": request_id, "tg_user_id": user_id, "tg_username": username or None,
            "sku": self.sku, "qty": 1, "provider": "sepay",
        })

    async def order(self, code: str, user_id: int):
        return await self.request("GET", f"/api/bot/orders/{quote(code, safe='')}", params={"tg_user_id": user_id})

    async def orders(self, user_id: int):
        data = await self.request("GET", "/api/bot/orders", params={"tg_user_id": user_id})
        return data.get("orders", [])

    async def next_delivery(self):
        data = await self.request("POST", "/api/bot/deliveries/next", {"sku": self.sku})
        return data.get("delivery")

    async def acknowledge(self, code: str, lease: str, message_id: int):
        return await self.request("POST", f"/api/bot/deliveries/{quote(code, safe='')}/ack", {
            "lease_token": lease, "telegram_message_id": message_id,
        })
