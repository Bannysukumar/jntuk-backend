"""Rate-limited HTTP client for the JNTUK results JSON API."""

from __future__ import annotations

import asyncio
import json
import time

import aiohttp
import requests

from config.redisConnection import redisConnection
from config.settings import (
    JNTUK_MAX_RETRIES,
    JNTUK_NOTIFICATIONS_CACHE_KEY,
    JNTUK_RATE_LIMIT_BACKOFF_SECONDS,
    JNTUK_REQUEST_DELAY_SECONDS,
    JNTUK_RESULTS_API_BASE,
    NOTIFICATIONS_EXPIRY_TIME,
)
from utils.logger import scraping_logger


class JntukRateLimitedError(RuntimeError):
    """Raised when JNTUK keeps returning HTTP 429 after retries."""


_request_lock = asyncio.Lock()
_last_request_at = 0.0


def _api_base(url: str | None = None) -> str:
    return (url or JNTUK_RESULTS_API_BASE).rstrip("/")


async def _wait_for_slot() -> None:
    global _last_request_at
    async with _request_lock:
        elapsed = time.monotonic() - _last_request_at
        remaining = JNTUK_REQUEST_DELAY_SECONDS - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
        _last_request_at = time.monotonic()


async def post_json(
    path: str,
    payload: dict | None = None,
    url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict:
    """POST JSON to a JNTUK results endpoint with spacing and 429 backoff."""
    endpoint = f"{_api_base(url)}/{path.lstrip('/')}"
    body = payload if payload is not None else {}
    last_error: Exception | None = None
    owns_session = session is None
    if session is None:
        session = aiohttp.ClientSession()

    try:
        for attempt in range(JNTUK_MAX_RETRIES):
            await _wait_for_slot()
            try:
                async with session.post(
                    endpoint,
                    json=body,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=20),
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                ) as response:
                    text = await response.text()
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        parsed = {"status": response.status, "message": text[:300]}
            except Exception as error:
                last_error = error
                scraping_logger.warning(f"JNTUK request failed {endpoint}: {error}")
                await asyncio.sleep(min(5 * (attempt + 1), 20))
                continue

            status = parsed.get("status", 0)
            if status == 429 or "too many requests" in str(parsed.get("message", "")).lower():
                scraping_logger.warning(
                    f"JNTUK rate-limited {endpoint}; backing off "
                    f"{JNTUK_RATE_LIMIT_BACKOFF_SECONDS}s"
                )
                await asyncio.sleep(JNTUK_RATE_LIMIT_BACKOFF_SECONDS)
                continue
            return parsed

        if last_error is not None:
            raise last_error
        raise JntukRateLimitedError(f"JNTUK rate-limited {endpoint}")
    finally:
        if owns_session:
            await session.close()


def post_json_sync(path: str, payload: dict | None = None, url: str | None = None) -> dict:
    endpoint = f"{_api_base(url)}/{path.lstrip('/')}"
    response = requests.post(
        endpoint,
        json=payload if payload is not None else {},
        timeout=15,
        verify=False,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    return response.json()


async def fetch_notifications(url: str | None = None, use_cache: bool = True) -> list[dict]:
    if use_cache and redisConnection.client:
        cached = redisConnection.client.get(JNTUK_NOTIFICATIONS_CACHE_KEY)
        if cached:
            return json.loads(
                cached.decode("utf-8") if isinstance(cached, bytes) else cached
            )

    payload = await post_json("getresultsnotifications", {}, url=url)
    notifications = payload.get("data") if payload.get("status") == 200 else None
    if not isinstance(notifications, list):
        return []

    if redisConnection.client:
        redisConnection.client.set(
            JNTUK_NOTIFICATIONS_CACHE_KEY,
            json.dumps(notifications),
            ex=NOTIFICATIONS_EXPIRY_TIME,
        )
    return notifications


async def fetch_student_results(
    hall_ticket: str,
    result_id: str,
    url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict:
    return await post_json(
        "getstudentresults",
        {
            "hall_ticket_no": hall_ticket,
            "notification_id": result_id,
            "resultid": result_id,
        },
        url=url,
        session=session,
    )
