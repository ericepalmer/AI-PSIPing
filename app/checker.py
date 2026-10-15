"""HTTP checks. A target is down on timeout, transport failure, or an HTTP error."""

from __future__ import annotations

import time

import httpx

from app.logic import is_up_status, timeout_error

USER_AGENT = "PSIPing/1.0"
MAX_BODY_BYTES = 64 * 1024
MAX_REDIRECTS = 10


async def check_url(client: httpx.AsyncClient, url: str, timeout_seconds: int) -> dict:
    started = time.perf_counter()
    timeout = httpx.Timeout(timeout_seconds)
    try:
        async with client.stream(
            "GET",
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        ) as response:
            read = 0
            async for chunk in response.aiter_bytes():
                read += len(chunk)
                if read >= MAX_BODY_BYTES:
                    break
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            status_code = response.status_code
            if is_up_status(status_code):
                return {
                    "status": "up",
                    "http_status": status_code,
                    "response_ms": elapsed_ms,
                    "error": None,
                }
            return {
                "status": "down",
                "http_status": status_code,
                "response_ms": elapsed_ms,
                "error": f"HTTP {status_code}",
            }
    except httpx.TimeoutException:
        return _down(started, timeout_error(timeout_seconds))
    except httpx.TooManyRedirects:
        return _down(started, f"More than {MAX_REDIRECTS} redirects")
    except httpx.ConnectError:
        return _down(started, "Connection failed")
    except httpx.RequestError:
        return _down(started, "Request failed")


def _down(started: float, error: str) -> dict:
    return {
        "status": "down",
        "http_status": None,
        "response_ms": int((time.perf_counter() - started) * 1000),
        "error": error,
    }
