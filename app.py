from __future__ import annotations

import argparse
import base64
import hmac
import json
import math
import os
import sqlite3
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

APP_DIR = Path(__file__).resolve().parent
VOLUME_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
DB_PATH = Path(os.environ.get("TRACE_DB_PATH") or (Path(VOLUME_PATH) / "trace_records.sqlite3" if VOLUME_PATH else APP_DIR / "trace_records.sqlite3"))
WEB_PATH = APP_DIR / "index.html"

# Transparent, editable starting assumptions. These are broad estimates, not device measurements.
AI_TEXT_WH = 0.24        # provider-specific median text prompt benchmark
SEARCH_WH = 0.30         # dated, broad public estimate for a web search
VIDEO_G_PER_HOUR = 55.0  # broad video streaming estimate including network/viewing device


def connect_db() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect_db() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_records (
                record_date TEXT PRIMARY KEY,
                instagram_hours REAL NOT NULL CHECK(instagram_hours BETWEEN 0 AND 24),
                youtube_hours REAL NOT NULL CHECK(youtube_hours BETWEEN 0 AND 24),
                ai_prompts INTEGER NOT NULL CHECK(ai_prompts BETWEEN 0 AND 100000),
                google_searches INTEGER NOT NULL CHECK(google_searches BETWEEN 0 AND 100000),
                grid_intensity REAL NOT NULL CHECK(grid_intensity BETWEEN 0 AND 2000),
                instagram_g REAL NOT NULL,
                youtube_g REAL NOT NULL,
                ai_g REAL NOT NULL,
                search_g REAL NOT NULL,
                total_g REAL NOT NULL,
                estimate_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)


def estimate(data: dict) -> dict:
    instagram_g = data["instagram_hours"] * VIDEO_G_PER_HOUR
    youtube_g = data["youtube_hours"] * VIDEO_G_PER_HOUR
    ai_g = data["ai_prompts"] * AI_TEXT_WH * data["grid_intensity"] / 1000
    search_g = data["google_searches"] * SEARCH_WH * data["grid_intensity"] / 1000
    return {
        "instagram_g": round(instagram_g, 4),
        "youtube_g": round(youtube_g, 4),
        "ai_g": round(ai_g, 4),
        "search_g": round(search_g, 4),
        "total_g": round(instagram_g + youtube_g + ai_g + search_g, 4),
    }


def serialize(row: sqlite3.Row) -> dict:
    item = dict(row)
    item["total_kg"] = round(item["total_g"] / 1000, 4)
    return item


class Handler(BaseHTTPRequestHandler):
    server_version = "TraceLocal/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def authorize(self):
        username = os.environ.get("TRACE_USERNAME")
        password = os.environ.get("TRACE_PASSWORD")
        if not username or not password:
            return True
        header = self.headers.get("Authorization", "")
        try:
            scheme, token = header.split(" ", 1)
            if scheme.lower() != "basic":
                raise ValueError("Missing basic authorization")
            supplied_user, supplied_password = base64.b64decode(token, validate=True).decode("utf-8").split(":", 1)
        except (ValueError, UnicodeDecodeError):
            supplied_user, supplied_password = "", ""
        valid = hmac.compare_digest(supplied_user, username) and hmac.compare_digest(supplied_password, password)
        if not valid:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Trace", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not self.authorize():
            return
        if parsed.path in ("/**", "/**/"):
            self.send_response(302)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if parsed.path == "/" or parsed.path == "/index.html":
            try:
                body = WEB_PATH.read_bytes()
            except OSError:
                self.send_error(500, "Could not read the app page")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/records":
            query = parse_qs(parsed.query)
            day = query.get("date", [None])[0]
            with connect_db() as db:
                if day:
                    try:
                        date.fromisoformat(day)
                    except ValueError:
                        self.send_json({"error": "Date must use YYYY-MM-DD."}, 400)
                        return
                    row = db.execute("SELECT * FROM daily_records WHERE record_date=?", (day,)).fetchone()
                    self.send_json({"record": serialize(row) if row else None})
                    return
                rows = db.execute("SELECT * FROM daily_records ORDER BY record_date DESC").fetchall()
                totals = db.execute("""
                    SELECT COUNT(*) AS days, COALESCE(SUM(total_g),0) AS total_g,
                           COALESCE(AVG(total_g),0) AS average_g,
                           COALESCE(MAX(total_g),0) AS peak_g
                    FROM daily_records
                """).fetchone()
                self.send_json({"records": [serialize(r) for r in rows], "summary": dict(totals)})
            return
        self.send_error(404)

    def do_POST(self):
        if not self.authorize():
            return
        if urlparse(self.path).path != "/api/records":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16_384:
                raise ValueError("Request body is missing or too large.")
            raw = json.loads(self.rfile.read(length))
            if not isinstance(raw, dict):
                raise ValueError("Expected a JSON object of daily activity.")
            day = date.fromisoformat(str(raw.get("date", "")))

            def finite_number(name, default=0):
                value = raw.get(name, default)
                if isinstance(value, bool):
                    raise ValueError("Enter numbers for each activity.")
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("Values must be finite numbers.")
                return number

            def whole_number(name):
                number = finite_number(name)
                if not number.is_integer():
                    raise ValueError(f"{name.replace('_', ' ').capitalize()} must be a whole number.")
                return int(number)

            values = {
                "instagram_hours": finite_number("instagram_hours"),
                "youtube_hours": finite_number("youtube_hours"),
                "ai_prompts": whole_number("ai_prompts"),
                "google_searches": whole_number("google_searches"),
                "grid_intensity": finite_number("grid_intensity", 400),
            }
            for name in ("instagram_hours", "youtube_hours", "grid_intensity"):
                if not 0 <= values[name] <= (2000 if name == "grid_intensity" else 24):
                    raise ValueError(f"{name.replace('_', ' ').capitalize()} is outside the allowed range.")
            for name in ("ai_prompts", "google_searches"):
                if not 0 <= values[name] <= 100000:
                    raise ValueError(f"{name.replace('_', ' ').capitalize()} must be between 0 and 100,000.")
            if any(isinstance(raw.get(n), bool) for n in ("instagram_hours", "youtube_hours", "ai_prompts", "google_searches", "grid_intensity")):
                raise ValueError("Enter numbers for each activity.")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc) or "Check the values and try again."}, 400)
            return

        totals = estimate(values)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with connect_db() as db:
            db.execute("""
                INSERT INTO daily_records (
                    record_date, instagram_hours, youtube_hours, ai_prompts, google_searches,
                    grid_intensity, instagram_g, youtube_g, ai_g, search_g, total_g,
                    estimate_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(record_date) DO UPDATE SET
                    instagram_hours=excluded.instagram_hours,
                    youtube_hours=excluded.youtube_hours,
                    ai_prompts=excluded.ai_prompts,
                    google_searches=excluded.google_searches,
                    grid_intensity=excluded.grid_intensity,
                    instagram_g=excluded.instagram_g,
                    youtube_g=excluded.youtube_g,
                    ai_g=excluded.ai_g,
                    search_g=excluded.search_g,
                    total_g=excluded.total_g,
                    estimate_version=excluded.estimate_version,
                    updated_at=excluded.updated_at
            """, (
                day.isoformat(), values["instagram_hours"], values["youtube_hours"],
                values["ai_prompts"], values["google_searches"], values["grid_intensity"],
                totals["instagram_g"], totals["youtube_g"], totals["ai_g"], totals["search_g"],
                totals["total_g"], now, now,
            ))
            row = db.execute("SELECT * FROM daily_records WHERE record_date=?", (day.isoformat(),)).fetchone()
        self.send_json({"record": serialize(row), "saved": True}, 201)


def main():
    parser = argparse.ArgumentParser(description="Run Trace, a local personal digital-activity tracker.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1, local computer only).")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")), help="Port (default: 8765).")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1") and not (os.environ.get("TRACE_USERNAME") and os.environ.get("TRACE_PASSWORD")):
        raise SystemExit("Set TRACE_USERNAME and TRACE_PASSWORD before binding Trace to a network interface.")
    init_db()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Trace is running at http://{args.host}:{args.port}")
    print(f"SQLite record database: {DB_PATH}")
    print("Press Ctrl+C to stop the server. Saved records remain in the database.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Trace…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

