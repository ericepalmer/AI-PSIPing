"""Local uptime monitor API and dashboard."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.checker import MAX_REDIRECTS, check_url
from app.db import (
    apply_layout,
    connect,
    delete_folder,
    delete_monitor,
    dismiss_alert,
    dismiss_all_alerts,
    due_monitors,
    get_folder,
    get_monitor,
    insert_folder,
    insert_monitor,
    list_checks,
    list_folders,
    list_monitors,
    open_alerts,
    record_check,
    update_folder,
    update_monitor,
)
from app.logic import (
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_INTERVAL_SECONDS,
    MAX_TIMEOUT_SECONDS,
    MIN_INTERVAL_SECONDS,
    MIN_TIMEOUT_SECONDS,
    chart_series,
    clamp_interval,
    clamp_timeout,
    iso,
    parse_time,
    summarize,
    utcnow,
    window_start,
)

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
DATA = ROOT / "data" / "psiping.db"

conn = connect(DATA)
db_lock = asyncio.Lock()
inflight: set[int] = set()


class MonitorIn(BaseModel):
    name: str | None = None
    url: str
    interval_seconds: int = Field(default=DEFAULT_INTERVAL_SECONDS)
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS)
    alert_screen: bool = True


class MonitorPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    interval_seconds: int | None = None
    timeout_seconds: int | None = None
    alert_screen: bool | None = None
    enabled: bool | None = None


class FolderIn(BaseModel):
    name: str = "Folder"


class FolderPatch(BaseModel):
    name: str | None = None
    collapsed: bool | None = None


class LayoutItem(BaseModel):
    type: str
    id: int
    children: list[int] = []


class LayoutIn(BaseModel):
    items: list[LayoutItem]


def normalize_url(raw: str) -> str:
    text = raw.strip()
    if not text:
        raise HTTPException(status_code=400, detail="URL is required")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Enter an http or https URL")
    return text


def display_name(name: str | None, url: str) -> str:
    cleaned = (name or "").strip()
    if cleaned:
        return cleaned[:120]
    host = urlparse(url).netloc
    return host or url


def validate_timing(interval: int | None, timeout: int | None) -> tuple[int | None, int | None]:
    if interval is not None and not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Interval must be between {MIN_INTERVAL_SECONDS}s and {MAX_INTERVAL_SECONDS}s",
        )
    if timeout is not None and not MIN_TIMEOUT_SECONDS <= timeout <= MAX_TIMEOUT_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Timeout must be between {MIN_TIMEOUT_SECONDS}s and {MAX_TIMEOUT_SECONDS}s",
        )
    return interval, timeout


async def run_check(monitor: dict, *, force: bool = False) -> None:
    if monitor["id"] in inflight:
        return
    inflight.add(monitor["id"])
    try:
        result = await check_url(app.state.client, monitor["url"], monitor["timeout_seconds"])
        result["checked_at"] = iso(utcnow())
        async with db_lock:
            current = get_monitor(conn, monitor["id"])
            if current is None or (not current["enabled"] and not force):
                return
            record_check(conn, current, result)
    finally:
        inflight.discard(monitor["id"])


async def scheduler() -> None:
    while True:
        async with db_lock:
            due = due_monitors(conn, utcnow())
        if due:
            await asyncio.gather(*(run_check(monitor) for monitor in due))
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(follow_redirects=True, max_redirects=MAX_REDIRECTS)
    task = asyncio.create_task(scheduler())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await app.state.client.aclose()


app = FastAPI(title="PSIPing", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/monitors")
async def monitors_list():
    async with db_lock:
        monitors = list_monitors(conn)
        folders = list_folders(conn)
        alerts = open_alerts(conn)
    return {"monitors": monitors, "folders": folders, "alerts": alerts}


@app.post("/api/monitors")
async def monitors_create(body: MonitorIn):
    url = normalize_url(body.url)
    validate_timing(body.interval_seconds, body.timeout_seconds)
    async with db_lock:
        monitor = insert_monitor(
            conn,
            name=display_name(body.name, url),
            url=url,
            interval_seconds=clamp_interval(body.interval_seconds),
            timeout_seconds=clamp_timeout(body.timeout_seconds),
            alert_screen=body.alert_screen,
        )
    asyncio.create_task(run_check(monitor))
    return monitor


@app.patch("/api/monitors/{monitor_id}")
async def monitors_update(monitor_id: int, body: MonitorPatch):
    fields = body.model_dump(exclude_unset=True)
    validate_timing(fields.get("interval_seconds"), fields.get("timeout_seconds"))
    async with db_lock:
        existing = get_monitor(conn, monitor_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Monitor not found")
        if "url" in fields and fields["url"] is not None:
            fields["url"] = normalize_url(fields["url"])
        if "name" in fields:
            fields["name"] = display_name(fields["name"], fields.get("url") or existing["url"])
        monitor = update_monitor(conn, monitor_id, fields)
    return monitor


@app.delete("/api/monitors/{monitor_id}")
async def monitors_delete(monitor_id: int):
    async with db_lock:
        if not delete_monitor(conn, monitor_id):
            raise HTTPException(status_code=404, detail="Monitor not found")
    return {"ok": True}


@app.post("/api/monitors/{monitor_id}/check")
async def monitors_check_now(monitor_id: int):
    async with db_lock:
        monitor = get_monitor(conn, monitor_id)
    if monitor is None:
        raise HTTPException(status_code=404, detail="Monitor not found")
    await run_check(monitor, force=True)
    async with db_lock:
        return get_monitor(conn, monitor_id)


@app.get("/api/monitors/{monitor_id}/history")
async def monitors_history(monitor_id: int, range_key: str = "24h"):
    if range_key not in {"1h", "24h", "7d", "30d", "all"}:
        raise HTTPException(status_code=400, detail="Unknown range")
    now = utcnow()
    start = window_start(range_key, now)
    async with db_lock:
        monitor = get_monitor(conn, monitor_id)
        if monitor is None:
            raise HTTPException(status_code=404, detail="Monitor not found")
        checks = list_checks(conn, monitor_id, since=iso(start) if start else None, limit=2000)
    if start is None and checks:
        start = parse_time(checks[0]["checked_at"])
    elif start is None:
        start = now
    stats = summarize(checks, now)
    recent = list(reversed(checks[-100:]))
    return {
        "monitor": monitor,
        "range": range_key,
        "stats": stats,
        "series": chart_series(checks, start, now),
        "checks": recent,
    }


def clean_folder_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Folder name is required")
    return cleaned[:120]


@app.post("/api/folders")
async def folders_create(body: FolderIn):
    async with db_lock:
        return insert_folder(conn, clean_folder_name(body.name))


@app.patch("/api/folders/{folder_id}")
async def folders_update(folder_id: int, body: FolderPatch):
    fields = body.model_dump(exclude_unset=True)
    if "name" in fields and fields["name"] is not None:
        fields["name"] = clean_folder_name(fields["name"])
    async with db_lock:
        if get_folder(conn, folder_id) is None:
            raise HTTPException(status_code=404, detail="Folder not found")
        return update_folder(conn, folder_id, fields)


@app.delete("/api/folders/{folder_id}")
async def folders_delete(folder_id: int):
    async with db_lock:
        if not delete_folder(conn, folder_id):
            raise HTTPException(status_code=404, detail="Folder not found")
    return {"ok": True}


@app.post("/api/layout")
async def layout_update(body: LayoutIn):
    async with db_lock:
        try:
            apply_layout(conn, [item.model_dump() for item in body.items])
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    return {"ok": True}


@app.post("/api/alerts/{alert_id}/dismiss")
async def alerts_dismiss(alert_id: int):
    async with db_lock:
        dismiss_alert(conn, alert_id)
    return {"ok": True}


@app.post("/api/alerts/dismiss-all")
async def alerts_dismiss_all():
    async with db_lock:
        dismiss_all_alerts(conn)
    return {"ok": True}
