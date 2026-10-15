"""SQLite storage for monitors, check history, and screen alerts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.logic import iso, parse_time, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    alert_screen INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_status TEXT,
    last_checked_at TEXT,
    last_response_ms INTEGER,
    last_http_status INTEGER,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    response_ms INTEGER,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_checks_monitor_time
    ON checks (monitor_id, checked_at);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitor_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL,
            collapsed INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(monitors)")}
    if "folder_id" not in columns:
        conn.execute("ALTER TABLE monitors ADD COLUMN folder_id INTEGER")
    if "sort_order" not in columns:
        conn.execute("ALTER TABLE monitors ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
        rows = conn.execute("SELECT id FROM monitors ORDER BY created_at ASC, id ASC").fetchall()
        for index, row in enumerate(rows):
            conn.execute("UPDATE monitors SET sort_order = ? WHERE id = ?", (index, row["id"]))
    conn.commit()


def monitor_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "interval_seconds": row["interval_seconds"],
        "timeout_seconds": row["timeout_seconds"],
        "alert_screen": bool(row["alert_screen"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "last_status": row["last_status"],
        "last_checked_at": row["last_checked_at"],
        "last_response_ms": row["last_response_ms"],
        "last_http_status": row["last_http_status"],
        "last_error": row["last_error"],
        "folder_id": row["folder_id"],
        "sort_order": row["sort_order"],
    }


def insert_monitor(
    conn: sqlite3.Connection,
    *,
    name: str,
    url: str,
    interval_seconds: int,
    timeout_seconds: int,
    alert_screen: bool,
) -> dict:
    now = iso(utcnow())
    cursor = conn.execute(
        """
        INSERT INTO monitors (
            name, url, interval_seconds, timeout_seconds, alert_screen, enabled, created_at,
            folder_id, sort_order
        ) VALUES (?, ?, ?, ?, ?, 1, ?, NULL, ?)
        """,
        (name, url, interval_seconds, timeout_seconds, int(alert_screen), now, _next_root_sort(conn)),
    )
    conn.commit()
    return get_monitor(conn, cursor.lastrowid)


def get_monitor(conn: sqlite3.Connection, monitor_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM monitors WHERE id = ?", (monitor_id,)).fetchone()
    return monitor_from_row(row) if row else None


def list_monitors(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM monitors ORDER BY created_at ASC, id ASC").fetchall()
    return [monitor_from_row(row) for row in rows]


def update_monitor(conn: sqlite3.Connection, monitor_id: int, fields: dict) -> dict | None:
    allowed = {
        "name",
        "url",
        "interval_seconds",
        "timeout_seconds",
        "alert_screen",
        "enabled",
    }
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in {"alert_screen", "enabled"}:
            value = int(bool(value))
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_monitor(conn, monitor_id)
    values.append(monitor_id)
    conn.execute(f"UPDATE monitors SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    return get_monitor(conn, monitor_id)


def delete_monitor(conn: sqlite3.Connection, monitor_id: int) -> bool:
    conn.execute("DELETE FROM alerts WHERE monitor_id = ?", (monitor_id,))
    cursor = conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
    conn.commit()
    return cursor.rowcount > 0


def due_monitors(conn: sqlite3.Connection, now) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM monitors WHERE enabled = 1 ORDER BY id ASC"
    ).fetchall()
    due = []
    for row in rows:
        monitor = monitor_from_row(row)
        last = monitor["last_checked_at"]
        if last is None or (now - parse_time(last)).total_seconds() >= monitor["interval_seconds"]:
            due.append(monitor)
    return due


def record_check(conn: sqlite3.Connection, monitor: dict, result: dict) -> dict | None:
    """Store a check, update the monitor, and open a screen alert on a status change."""
    checked_at = result["checked_at"]
    previous = monitor["last_status"]
    status = result["status"]
    conn.execute(
        """
        INSERT INTO checks (monitor_id, checked_at, status, http_status, response_ms, error)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            monitor["id"],
            checked_at,
            status,
            result.get("http_status"),
            result.get("response_ms"),
            result.get("error"),
        ),
    )
    conn.execute(
        """
        UPDATE monitors
        SET last_status = ?, last_checked_at = ?, last_response_ms = ?,
            last_http_status = ?, last_error = ?
        WHERE id = ?
        """,
        (
            status,
            checked_at,
            result.get("response_ms"),
            result.get("http_status"),
            result.get("error"),
            monitor["id"],
        ),
    )
    alert = None
    became_down = status == "down" and previous != "down"
    recovered = status == "up" and previous == "down"
    if monitor["alert_screen"] and (became_down or recovered):
        if status == "down":
            kind = "down"
            message = result.get("error") or "Check failed"
        else:
            kind = "recovered"
            message = "Responding again"
        cursor = conn.execute(
            """
            INSERT INTO alerts (monitor_id, kind, message, created_at, dismissed)
            VALUES (?, ?, ?, ?, 0)
            """,
            (monitor["id"], kind, message, checked_at),
        )
        alert = {
            "id": cursor.lastrowid,
            "monitor_id": monitor["id"],
            "name": monitor["name"],
            "url": monitor["url"],
            "kind": kind,
            "message": message,
            "created_at": checked_at,
            "dismissed": False,
        }
    conn.commit()
    return alert


def list_checks(
    conn: sqlite3.Connection,
    monitor_id: int,
    since: str | None = None,
    limit: int = 500,
) -> list[dict]:
    if since:
        rows = conn.execute(
            """
            SELECT * FROM checks
            WHERE monitor_id = ? AND checked_at >= ?
            ORDER BY checked_at ASC
            """,
            (monitor_id, since),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM checks
            WHERE monitor_id = ?
            ORDER BY checked_at DESC
            LIMIT ?
            """,
            (monitor_id, limit),
        ).fetchall()
        rows = list(reversed(rows))
    return [
        {
            "id": row["id"],
            "monitor_id": row["monitor_id"],
            "checked_at": row["checked_at"],
            "status": row["status"],
            "http_status": row["http_status"],
            "response_ms": row["response_ms"],
            "error": row["error"],
        }
        for row in rows
    ]


def open_alerts(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT alerts.*, monitors.name, monitors.url
        FROM alerts
        JOIN monitors ON monitors.id = alerts.monitor_id
        WHERE alerts.dismissed = 0
        ORDER BY alerts.id ASC
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "monitor_id": row["monitor_id"],
            "name": row["name"],
            "url": row["url"],
            "kind": row["kind"],
            "message": row["message"],
            "created_at": row["created_at"],
            "dismissed": False,
        }
        for row in rows
    ]


def dismiss_alert(conn: sqlite3.Connection, alert_id: int) -> None:
    conn.execute("UPDATE alerts SET dismissed = 1 WHERE id = ?", (alert_id,))
    conn.commit()


def dismiss_all_alerts(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE alerts SET dismissed = 1 WHERE dismissed = 0")
    conn.commit()


def _next_root_sort(conn: sqlite3.Connection) -> int:
    folder_max = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS value FROM folders").fetchone()["value"]
    monitor_max = conn.execute(
        "SELECT COALESCE(MAX(sort_order), -1) AS value FROM monitors WHERE folder_id IS NULL"
    ).fetchone()["value"]
    return max(folder_max, monitor_max) + 1


def folder_from_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "sort_order": row["sort_order"],
        "collapsed": bool(row["collapsed"]),
    }


def list_folders(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM folders ORDER BY sort_order ASC, id ASC").fetchall()
    return [folder_from_row(row) for row in rows]


def get_folder(conn: sqlite3.Connection, folder_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()
    return folder_from_row(row) if row else None


def insert_folder(conn: sqlite3.Connection, name: str) -> dict:
    cursor = conn.execute(
        "INSERT INTO folders (name, sort_order, collapsed) VALUES (?, ?, 0)",
        (name, _next_root_sort(conn)),
    )
    conn.commit()
    return get_folder(conn, cursor.lastrowid)


def update_folder(conn: sqlite3.Connection, folder_id: int, fields: dict) -> dict | None:
    allowed = {"name", "collapsed"}
    sets = []
    values = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "collapsed":
            value = int(bool(value))
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return get_folder(conn, folder_id)
    values.append(folder_id)
    conn.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    return get_folder(conn, folder_id)


def layout_snapshot(conn: sqlite3.Connection) -> list[dict]:
    folders = list_folders(conn)
    monitors = list_monitors(conn)
    items = []
    for folder in folders:
        children = sorted(
            (monitor for monitor in monitors if monitor["folder_id"] == folder["id"]),
            key=lambda monitor: (monitor["sort_order"], monitor["id"]),
        )
        items.append(
            {
                "type": "folder",
                "id": folder["id"],
                "sort": folder["sort_order"],
                "children": [monitor["id"] for monitor in children],
            }
        )
    for monitor in monitors:
        if monitor["folder_id"] is None:
            items.append(
                {
                    "type": "monitor",
                    "id": monitor["id"],
                    "sort": monitor["sort_order"],
                    "children": [],
                }
            )
    items.sort(key=lambda item: (item["sort"], 0 if item["type"] == "folder" else 1, item["id"]))
    return [{"type": item["type"], "id": item["id"], "children": item["children"]} for item in items]


def apply_layout(conn: sqlite3.Connection, items: list[dict]) -> None:
    folder_ids = {row["id"] for row in conn.execute("SELECT id FROM folders")}
    monitor_ids = {row["id"] for row in conn.execute("SELECT id FROM monitors")}
    seen_folders: list[int] = []
    seen_monitors: list[int] = []
    for item in items:
        kind = item.get("type")
        item_id = item.get("id")
        children = item.get("children") or []
        if kind == "folder":
            if item_id not in folder_ids or item_id in seen_folders:
                raise ValueError("Unknown folder in layout")
            seen_folders.append(item_id)
            for child_id in children:
                if child_id not in monitor_ids or child_id in seen_monitors:
                    raise ValueError("Unknown site in layout")
                seen_monitors.append(child_id)
        elif kind == "monitor":
            if children:
                raise ValueError("A site cannot contain other items")
            if item_id not in monitor_ids or item_id in seen_monitors:
                raise ValueError("Unknown site in layout")
            seen_monitors.append(item_id)
        else:
            raise ValueError("Unknown layout item")
    if set(seen_folders) != folder_ids or set(seen_monitors) != monitor_ids:
        raise ValueError("Layout must include every folder and site")
    for index, item in enumerate(items):
        if item["type"] == "folder":
            conn.execute("UPDATE folders SET sort_order = ? WHERE id = ?", (index, item["id"]))
            for child_index, child_id in enumerate(item.get("children") or []):
                conn.execute(
                    "UPDATE monitors SET folder_id = ?, sort_order = ? WHERE id = ?",
                    (item["id"], child_index, child_id),
                )
        else:
            conn.execute(
                "UPDATE monitors SET folder_id = NULL, sort_order = ? WHERE id = ?",
                (index, item["id"]),
            )
    conn.commit()


def delete_folder(conn: sqlite3.Connection, folder_id: int) -> bool:
    if get_folder(conn, folder_id) is None:
        return False
    items = layout_snapshot(conn)
    rewritten = []
    for item in items:
        if item["type"] == "folder" and item["id"] == folder_id:
            rewritten.extend({"type": "monitor", "id": child_id, "children": []} for child_id in item["children"])
        else:
            rewritten.append(item)
    conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    apply_layout(conn, rewritten)
    return True
