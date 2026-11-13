#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""貓咪數獨多裝置共用成績版。

環境變數：
  DATABASE_PATH              SQLite 路徑，預設 data/neko-sudoku.db（Vercel 為 /tmp/neko-sudoku.db）
  TURSO_DATABASE_URL         設定後改用 Turso 遠端資料庫（Vercel 使用）
  TURSO_AUTH_TOKEN           Turso 資料庫權杖
  APP_TIMEZONE_OFFSET_HOURS  本日排名使用的時區位移，預設 8（台灣）
  TRUST_PROXY                部署在反向代理後方時設為 1
"""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any

from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    import turso_serverless
except ImportError:
    turso_serverless = None

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
DEFAULT_DB_PATH = BASE_DIR / "data" / "neko-sudoku.db"
if os.environ.get("VERCEL"):
    DEFAULT_DB_PATH = Path(tempfile.gettempdir()) / "neko-sudoku.db"
CONFIGURED_DB_PATH = os.environ.get("DATABASE_PATH", "").strip()
DB_PATH = Path(CONFIGURED_DB_PATH) if CONFIGURED_DB_PATH else DEFAULT_DB_PATH
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "").strip()
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
USE_TURSO = bool(TURSO_DATABASE_URL)

TIMEZONE_OFFSET_HOURS = int(os.environ.get("APP_TIMEZONE_OFFSET_HOURS", "8"))
APP_TIMEZONE = timezone(timedelta(hours=TIMEZONE_OFFSET_HOURS))
SIZES = tuple(range(4, 14))
CHALLENGE_VARIANTS = 3
KEY_PATTERN = re.compile(r"^(\d{1,2})-([1-3])$")

MAX_NAME_LENGTH = 12
MIN_TIME_MS = 100
MAX_TIME_MS = 4 * 60 * 60 * 1000
MAX_ATTEMPT = 1_000_000
MAX_MISTAKES = 100_000

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
app.json.ensure_ascii = False

if os.environ.get("TRUST_PROXY", "0").lower() in {"1", "true", "yes"}:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status = int(status)


def now_local() -> datetime:
    return datetime.now(APP_TIMEZONE)


def today_stamp() -> str:
    return now_local().strftime("%Y-%m-%d")


def clean_key(value: object) -> tuple[str, int, int]:
    if not isinstance(value, str):
        raise ApiError("題目代碼格式不正確。")
    match = KEY_PATTERN.fullmatch(value.strip())
    if not match:
        raise ApiError("題目代碼格式不正確。")
    size = int(match.group(1))
    variant = int(match.group(2))
    if size not in SIZES or variant < 1 or variant > CHALLENGE_VARIANTS:
        raise ApiError("題目代碼不在允許範圍內。")
    return f"{size}-{variant}", size, variant


def clean_name(value: object) -> str:
    if not isinstance(value, str):
        raise ApiError("玩家名字格式不正確。")
    value = value.strip()
    if not value:
        raise ApiError("請輸入玩家名字。")
    if len(value) > MAX_NAME_LENGTH:
        raise ApiError(f"玩家名字最多 {MAX_NAME_LENGTH} 個字。")
    if any(ord(char) < 32 for char in value):
        raise ApiError("玩家名字含有不允許的字元。")
    return value


def clean_int(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ApiError(f"{field}格式不正確。")
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ApiError(f"{field}格式不正確。") from None
    if number < minimum or number > maximum:
        raise ApiError(f"{field}超出允許範圍。")
    return number


def resolve_db_path() -> Path:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DB_PATH


def open_db() -> Any:
    if USE_TURSO:
        if turso_serverless is None:
            raise RuntimeError("已設定 TURSO_DATABASE_URL，但未安裝 turso_serverless。")
        connection = turso_serverless.connect(
            TURSO_DATABASE_URL,
            auth_token=TURSO_AUTH_TOKEN or None,
        )
        connection.row_factory = lambda _cursor, row: row
        return connection

    connection = sqlite3.connect(str(resolve_db_path()), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with closing(open_db()) as connection, connection:
        if not USE_TURSO:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scores (
                id              TEXT PRIMARY KEY,
                puzzle_key      TEXT NOT NULL,
                size            INTEGER NOT NULL,
                variant         INTEGER NOT NULL,
                player_name     TEXT NOT NULL,
                time_ms         INTEGER NOT NULL,
                completed_at_ms INTEGER NOT NULL,
                completed_date  TEXT NOT NULL,
                attempt         INTEGER NOT NULL,
                mistakes        INTEGER NOT NULL,
                created_at      TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scores_puzzle_time
            ON scores (puzzle_key, time_ms, completed_at_ms, id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scores_date_order
            ON scores (completed_date, size, variant, time_ms, completed_at_ms, id)
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS challenge_sessions (
                id              TEXT PRIMARY KEY,
                puzzle_key      TEXT NOT NULL,
                player_name     TEXT NOT NULL,
                started_at_ms   INTEGER NOT NULL,
                used_at_ms      INTEGER,
                created_at      TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_key_created
            ON challenge_sessions (puzzle_key, created_at)
            """
        )


def get_db() -> Any:
    if "db" not in g:
        g.db = open_db()
    return g.db


@app.teardown_appcontext
def close_db(_error: object = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def row_to_score(row: Any) -> dict:
    return {
        "id": row["id"],
        "name": row["player_name"],
        "time": row["time_ms"],
        "at": row["completed_at_ms"],
        "attempt": row["attempt"],
        "mistakes": row["mistakes"],
    }


def score_order_sql() -> str:
    return "ORDER BY time_ms ASC, completed_at_ms ASC, id ASC"


@app.errorhandler(ApiError)
def handle_api_error(error: ApiError):
    return jsonify({"error": error.message}), error.status


@app.errorhandler(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
def handle_too_large(_error):
    return jsonify({"error": "請求內容過大。"}), HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def handle_database_error(error: Exception):
    app.logger.exception("Database error: %s", error)
    return jsonify({"error": "資料庫忙碌中，請稍後再試。"}), HTTPStatus.SERVICE_UNAVAILABLE


app.register_error_handler(sqlite3.OperationalError, handle_database_error)
if turso_serverless is not None:
    app.register_error_handler(turso_serverless.OperationalError, handle_database_error)


@app.after_request
def add_headers(response: Response) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/health")
def api_health():
    get_db().execute("SELECT 1").fetchone()
    return jsonify(
        {
            "ok": True,
            "date": today_stamp(),
            "storage": "turso" if USE_TURSO else "sqlite",
        }
    )


@app.get("/api/scores")
def api_scores_for_key():
    key, _size, _variant = clean_key(request.args.get("key"))
    limit = clean_int(request.args.get("limit", "3"), "筆數", 1, 100)
    db = get_db()
    total = db.execute(
        "SELECT COUNT(*) FROM scores WHERE puzzle_key = ?",
        (key,),
    ).fetchone()[0]
    rows = db.execute(
        f"""
        SELECT * FROM scores
        WHERE puzzle_key = ?
        {score_order_sql()}
        LIMIT ?
        """,
        (key, limit),
    ).fetchall()
    scores = [row_to_score(row) for row in rows]
    return jsonify(
        {
            "key": key,
            "total": total,
            "best": scores[0] if scores else None,
            "scores": scores,
        }
    )


@app.get("/api/scores/today")
def api_scores_today():
    date = today_stamp()
    rows = get_db().execute(
        f"""
        SELECT * FROM scores
        WHERE completed_date = ?
        ORDER BY size ASC, variant ASC, time_ms ASC, completed_at_ms ASC, id ASC
        """,
        (date,),
    ).fetchall()

    groups: list[dict] = []
    current: dict | None = None
    for row in rows:
        if current is None or current["key"] != row["puzzle_key"]:
            current = {
                "key": row["puzzle_key"],
                "size": row["size"],
                "variant": row["variant"],
                "count": 0,
                "best": row["time_ms"],
                "scores": [],
            }
            groups.append(current)
        current["count"] += 1
        current["scores"].append(row_to_score(row))

    return jsonify({"date": date, "groups": groups})


@app.post("/api/challenge/start")
def api_challenge_start():
    payload = request.get_json(silent=True) or {}
    key, size, variant = clean_key(payload.get("key"))
    name = clean_name(payload.get("name"))

    started = now_local()
    started_at_ms = int(started.timestamp() * 1000)
    session_id = uuid.uuid4().hex
    db = get_db()
    with db:
        db.execute(
            """
            INSERT INTO challenge_sessions (
                id, puzzle_key, player_name, started_at_ms, used_at_ms, created_at
            ) VALUES (?, ?, ?, ?, NULL, ?)
            """,
            (
                session_id,
                key,
                name,
                started_at_ms,
                started.isoformat(timespec="seconds"),
            ),
        )

    return jsonify({
        "session_id": session_id,
        "started_at_ms": started_at_ms,
        "key": key,
        "size": size,
        "variant": variant,
    }), HTTPStatus.CREATED


@app.post("/api/scores")
def api_add_score():
    payload = request.get_json(silent=True) or {}
    key, size, variant = clean_key(payload.get("key"))
    name = clean_name(payload.get("name"))
    session_id = payload.get("session")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ApiError("缺少挑戰計時資訊，請重新開始挑戰。")
    session_id = session_id.strip()
    attempt = clean_int(payload.get("attempt"), "挑戰次數", 1, MAX_ATTEMPT)
    mistakes = clean_int(payload.get("mistakes", 0), "失誤次數", 0, MAX_MISTAKES)

    db = get_db()
    session = db.execute(
        "SELECT * FROM challenge_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if session is None:
        raise ApiError("找不到這場挑戰，請重新開始。")
    if session["puzzle_key"] != key or session["player_name"] != name:
        raise ApiError("挑戰資料不一致，請重新開始。")
    if session["used_at_ms"] is not None:
        raise ApiError("這場挑戰的成績已經送出過了。")

    completed = now_local()
    completed_at_ms = int(completed.timestamp() * 1000)
    started_at_ms = int(session["started_at_ms"])
    time_ms = completed_at_ms - started_at_ms
    if time_ms < MIN_TIME_MS:
        raise ApiError("完成時間太短，請重新挑戰。")
    if time_ms > MAX_TIME_MS:
        raise ApiError("完成時間超過上限，請重新挑戰。")

    entry = {
        "id": uuid.uuid4().hex,
        "puzzle_key": key,
        "size": size,
        "variant": variant,
        "player_name": name,
        "time_ms": time_ms,
        "completed_at_ms": completed_at_ms,
        "completed_date": completed.strftime("%Y-%m-%d"),
        "attempt": attempt,
        "mistakes": mistakes,
        "created_at": completed.isoformat(timespec="seconds"),
    }

    with db:
        updated = db.execute(
            "UPDATE challenge_sessions SET used_at_ms = ? WHERE id = ? AND used_at_ms IS NULL",
            (completed_at_ms, session_id),
        )
        if getattr(updated, "rowcount", -1) == 0:
            raise ApiError("這場挑戰的成績已經送出過了。")
        db.execute(
            """
            INSERT INTO scores (
                id, puzzle_key, size, variant, player_name, time_ms,
                completed_at_ms, completed_date, attempt, mistakes, created_at
            ) VALUES (
                :id, :puzzle_key, :size, :variant, :player_name, :time_ms,
                :completed_at_ms, :completed_date, :attempt, :mistakes, :created_at
            )
            """,
            entry,
        )

    rank = db.execute(
        """
        SELECT COUNT(*) + 1 FROM scores
        WHERE puzzle_key = ? AND (
            time_ms < ? OR
            (time_ms = ? AND completed_at_ms < ?) OR
            (time_ms = ? AND completed_at_ms = ? AND id < ?)
        )
        """,
        (
            key,
            entry["time_ms"],
            entry["time_ms"],
            entry["completed_at_ms"],
            entry["time_ms"],
            entry["completed_at_ms"],
            entry["id"],
        ),
    ).fetchone()[0]
    total = db.execute(
        "SELECT COUNT(*) FROM scores WHERE puzzle_key = ?",
        (key,),
    ).fetchone()[0]
    top_rows = db.execute(
        f"""
        SELECT * FROM scores
        WHERE puzzle_key = ?
        {score_order_sql()}
        LIMIT 5
        """,
        (key,),
    ).fetchall()

    saved = db.execute("SELECT * FROM scores WHERE id = ?", (entry["id"],)).fetchone()
    return jsonify(
        {
            "entry": row_to_score(saved),
            "rank": rank,
            "total": total,
            "scores": [row_to_score(row) for row in top_rows],
        }
    ), HTTPStatus.CREATED


@app.get("/")
@app.get("/index.html")
@app.get("/neko-sudoku.html")
def index_page():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.get("/favicon.ico")
def favicon():
    return Response(status=HTTPStatus.NO_CONTENT)


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"貓咪數獨已啟動：http://127.0.0.1:{port}/")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
