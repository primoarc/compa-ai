"""Cliente HTTP con ritmo, backoff y corte por tienda.

Cada tienda tiene su propio límite de concurrencia y una pausa mínima entre
peticiones. Un 429 o 5xx espera (respetando `Retry-After`) y reintenta con
backoff exponencial. Después de MAX_CONSECUTIVE_ERRORS fallos seguidos el
cliente se "abre" y deja de pedir: mejor una corrida parcial que insistir
contra una tienda que nos está pidiendo parar.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger("gt_compare.ingest")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124 Safari/537.36"
    ),
    "Accept-Language": "es-GT,es;q=0.9,en;q=0.7",
}

RETRY_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}
# Cabeceras cuyo valor no se escribe en el log (el de Actions es público).
REDACT_HEADERS = {"set-cookie", "cookie", "authorization"}
FIRST_ERROR_BODY = 300
MAX_RETRIES = 4
MAX_CONSECUTIVE_ERRORS = 8


class StoreBlocked(RuntimeError):
    """La tienda falló demasiadas veces seguidas; se corta la corrida."""


class PoliteClient:
    def __init__(
        self,
        store_key: str,
        *,
        concurrency: int = 3,
        min_interval: float = 0.25,
        timeout: float = 25.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        sleep=asyncio.sleep,
    ):
        self.store_key = store_key
        self._sem = asyncio.Semaphore(concurrency)
        self._min_interval = min_interval
        self._last = 0.0
        self._pace_lock = asyncio.Lock()
        self._consecutive = 0
        self._sleep = sleep
        self.requests = 0
        self.retries = 0
        self.failures = 0
        self._described: set[int] = set()   # códigos 4xx ya registrados con detalle
        self._client = httpx.AsyncClient(
            headers=DEFAULT_HEADERS, timeout=timeout, follow_redirects=True, transport=transport
        )

    async def __aenter__(self) -> "PoliteClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    @property
    def blocked(self) -> bool:
        return self._consecutive >= MAX_CONSECUTIVE_ERRORS

    async def _pace(self) -> None:
        async with self._pace_lock:
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await self._sleep(wait)
            self._last = time.monotonic()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self.blocked:
            raise StoreBlocked(f"{self.store_key}: demasiados errores seguidos")
        attempt = 0
        while True:
            async with self._sem:
                await self._pace()
                self.requests += 1
                try:
                    resp = await self._client.request(method, url, **kwargs)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    resp = None
                    error: Any = exc
                else:
                    error = None
            if resp is not None and resp.status_code not in RETRY_STATUS:
                if resp.status_code < 400:
                    self._consecutive = 0
                else:
                    self._consecutive += 1
                    self.failures += 1
                    # server y el inicio del cuerpo distinguen un WAF (Akamai, Cloudflare)
                    # de un error de la aplicación.
                    snippet = " ".join(resp.text[:120].split()) if resp.text else ""
                    logger.warning("%s %s -> %s server=%s %s", self.store_key, url[:100],
                                   resp.status_code, resp.headers.get("server", "(sin cabecera)"),
                                   snippet or "(cuerpo vacío)")
                    self._describe(resp)
                return resp
            attempt += 1
            if attempt > MAX_RETRIES:
                self._consecutive += 1
                self.failures += 1
                if resp is not None:
                    return resp
                raise error
            self.retries += 1
            delay = min(60.0, (2 ** attempt) + random.uniform(0, 1))
            if resp is not None:
                retry_after = resp.headers.get("retry-after")
                if retry_after and retry_after.isdigit():
                    delay = min(120.0, float(retry_after))
                logger.warning("%s %s -> %s, reintento %s en %.1fs",
                               self.store_key, url[:80], resp.status_code, attempt, delay)
            else:
                logger.warning("%s %s -> %s, reintento %s en %.1fs",
                               self.store_key, url[:80], type(error).__name__, attempt, delay)
            await self._sleep(delay)

    def _describe(self, resp: httpx.Response) -> None:
        """La primera vez que la tienda responde un código 4xx: todas las
        cabeceras y el inicio del cuerpo, para saber quién contestó."""
        if resp.status_code in self._described:
            return
        self._described.add(resp.status_code)
        headers = {k: ("(omitida)" if k.lower() in REDACT_HEADERS else v)
                   for k, v in resp.headers.items()}
        body = resp.text[:FIRST_ERROR_BODY] if resp.text else ""
        logger.warning("%s primer %s: cabeceras=%s cuerpo(%s bytes)=%r", self.store_key,
                       resp.status_code, headers, len(resp.content), body)

    async def pause(self, seconds: float) -> None:
        """Espera sin pedir nada (respeta el `sleep` inyectado en tests)."""
        await self._sleep(seconds)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)
