#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""八大智能數學邏輯組活動填答與即時展示系統。

只使用 Python 標準函式庫，啟動後以資料夾內的 data/store.json 儲存資料。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import socket
import sys
import threading
import traceback
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

BASE_DIR = Path(__file__).resolve().parent

TITLE = "【八大智能數學邏輯組---你的生活想法被數學邏輯影響嗎?】"

CATEGORIES = [
    "1. 我覺得這個習慣/做法很具有數學邏輯",
    "2. 我受不了這些沒有數學/邏輯的行為",
    "3. 我會運用數學邏輯的思維進行什麼事?",
    "4. 如果我的數學邏輯更強一點，在【什麼事上】應該可以更如魚得水?",
    "5. 其他【我就是有話要說】",
]

PAGE_PATHS = {
    "/",
    "/join",
    "/watch",
    "/watch/live",
    "/watch/categories",
    "/watch/category",
    "/admin",
}

STATIC_FILES = {
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/vendor/qrcode.min.js": ("vendor/qrcode.min.js", "application/javascript; charset=utf-8"),
}


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.message = message
        self.status = int(status)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_text(value: object, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ApiError(f"{field}格式不正確。")
    value = value.strip()
    if not value:
        raise ApiError(f"請填寫{field}。")
    if len(value) > max_length:
        raise ApiError(f"{field}最多 {max_length} 個字。")
    return value


def clean_category(value: object) -> int:
    try:
        category = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ApiError("請選擇一個回答類別。") from None
    if category < 1 or category > len(CATEGORIES):
        raise ApiError("回答類別不正確。")
    return category


def get_lan_ip() -> str:
    candidates: list[str] = []
    for target in (("8.8.8.8", 80), ("1.1.1.1", 80)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(target)
            candidates.append(sock.getsockname()[0])
        except OSError:
            pass
        finally:
            sock.close()

    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    for ip in candidates:
        if ip and not ip.startswith("127.") and not ip.startswith("169.254.") and ip != "0.0.0.0":
            return ip
    return "127.0.0.1"


def make_base_url(host: str, port: int, public_url: str) -> str:
    if public_url:
        return public_url.rstrip("/")
    if host in {"127.0.0.1", "localhost"}:
        return f"http://127.0.0.1:{port}"
    return f"http://{get_lan_ip()}:{port}"


class Store:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.data: dict = {"participants": {}, "responses": [], "nextSeq": 1}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            with self.lock:
                self._save_locked()
            return

        try:
            with self.path.open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError("資料檔格式不是物件")
            self.data = {
                "participants": loaded.get("participants", {}),
                "responses": loaded.get("responses", []),
                "nextSeq": int(loaded.get("nextSeq", 1)),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            backup = self.path.with_name(f"{self.path.stem}.broken-{datetime.now():%Y%m%d-%H%M%S}.json")
            try:
                os.replace(self.path, backup)
                print(f"警告：資料檔讀取失敗，已備份為 {backup}（{exc}）")
            except OSError:
                print(f"警告：資料檔讀取失敗：{exc}")
            with self.lock:
                self._save_locked()

        if not isinstance(self.data.get("participants"), dict):
            self.data["participants"] = {}
        if not isinstance(self.data.get("responses"), list):
            self.data["responses"] = []
        max_seq = max((int(r.get("seq", 0)) for r in self.data["responses"] if isinstance(r, dict)), default=0)
        self.data["nextSeq"] = max(int(self.data.get("nextSeq", 1)), max_seq + 1)

    def _save_locked(self) -> None:
        temp = self.path.with_name(f"{self.path.name}.tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
        os.replace(temp, self.path)

    def create_participant(self, nickname: str) -> dict:
        with self.lock:
            participant_id = uuid.uuid4().hex
            participant = {
                "id": participant_id,
                "nickname": nickname,
                "createdAt": now_iso(),
                "finishedAt": None,
            }
            self.data["participants"][participant_id] = participant
            self._save_locked()
            return dict(participant)

    def finish_participant(self, participant_id: str) -> None:
        with self.lock:
            participant = self.data["participants"].get(participant_id)
            if participant is None:
                raise ApiError("找不到參加者資料。", HTTPStatus.NOT_FOUND)
            participant["finishedAt"] = now_iso()
            self._save_locked()

    def add_response(self, participant_id: str, category: int, text: str) -> dict:
        with self.lock:
            participant = self.data["participants"].get(participant_id)
            if participant is None:
                raise ApiError("找不到參加者資料，請重新輸入暱稱。", HTTPStatus.NOT_FOUND)

            seq = int(self.data["nextSeq"])
            self.data["nextSeq"] = seq + 1
            response = {
                "id": uuid.uuid4().hex,
                "seq": seq,
                "participantId": participant_id,
                "nickname": participant["nickname"],
                "category": category,
                "text": text,
                "createdAt": now_iso(),
                "highlight": False,
                "adminNote": "",
            }
            self.data["responses"].append(response)
            self._save_locked()
            return dict(response)

    def update_response(self, response_id: str, payload: dict) -> dict:
        with self.lock:
            response = self._find_response(response_id)
            if "highlight" in payload:
                response["highlight"] = bool(payload["highlight"])
            if "adminNote" in payload:
                note = payload["adminNote"]
                if not isinstance(note, str):
                    raise ApiError("備註格式不正確。")
                response["adminNote"] = note.strip()[:2000]
            self._save_locked()
            return dict(response)

    def delete_response(self, response_id: str) -> None:
        with self.lock:
            before = len(self.data["responses"])
            self.data["responses"] = [
                item for item in self.data["responses"] if item.get("id") != response_id
            ]
            if len(self.data["responses"]) == before:
                raise ApiError("找不到這則回覆。", HTTPStatus.NOT_FOUND)
            self._save_locked()

    def _find_response(self, response_id: str) -> dict:
        for response in self.data["responses"]:
            if response.get("id") == response_id:
                return response
        raise ApiError("找不到這則回覆。", HTTPStatus.NOT_FOUND)

    def get_responses(
        self, after: int = 0, category: int | None = None, highlight: bool | None = None
    ) -> tuple[list[dict], int]:
        with self.lock:
            items = []
            for response in self.data["responses"]:
                if int(response.get("seq", 0)) <= after:
                    continue
                if category is not None and int(response.get("category", 0)) != category:
                    continue
                if highlight is not None and bool(response.get("highlight")) != highlight:
                    continue
                items.append(dict(response))
            items.sort(key=lambda item: int(item.get("seq", 0)))
            return items, int(self.data["nextSeq"]) - 1

    def all_responses(self) -> list[dict]:
        with self.lock:
            return [dict(item) for item in self.data["responses"]]

    def summary(self) -> dict:
        with self.lock:
            counts = {str(index): 0 for index in range(1, len(CATEGORIES) + 1)}
            latest_by_category: dict[str, str] = {}
            highlighted = 0
            finished = 0
            for participant in self.data["participants"].values():
                if participant.get("finishedAt"):
                    finished += 1
            for response in self.data["responses"]:
                key = str(response.get("category", ""))
                if key in counts:
                    counts[key] += 1
                    latest_by_category[key] = response.get("createdAt", "")
                if response.get("highlight"):
                    highlighted += 1
            return {
                "totalResponses": len(self.data["responses"]),
                "participants": len(self.data["participants"]),
                "finishedParticipants": finished,
                "highlighted": highlighted,
                "categoryCounts": counts,
                "latestByCategory": latest_by_category,
                "latestSeq": int(self.data["nextSeq"]) - 1,
            }


class AppServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler, store: Store) -> None:
        super().__init__(address, handler)
        self.store = store
        self.base_url = ""


class Handler(BaseHTTPRequestHandler):
    server_version = "MathLogicSurvey/1.0"

    @property
    def store(self) -> Store:
        return self.server.store  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {self.address_string()} {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._handle_get()
        except Exception as exc:  # pragma: no cover - 錯誤輸出保護
            self._handle_exception(exc)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._handle_post()
        except Exception as exc:  # pragma: no cover
            self._handle_exception(exc)

    def do_PATCH(self) -> None:  # noqa: N802
        try:
            self._handle_patch()
        except Exception as exc:  # pragma: no cover
            self._handle_exception(exc)

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self._handle_delete()
        except Exception as exc:  # pragma: no cover
            self._handle_exception(exc)

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, ApiError):
            self._send_json({"error": exc.message}, exc.status)
            return
        traceback.print_exc()
        try:
            self._send_json({"error": "伺服器發生錯誤，請稍後再試。"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        except OSError:
            pass

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/config":
            self._send_json(
                {
                    "title": TITLE,
                    "baseUrl": self.server.base_url,  # type: ignore[attr-defined]
                    "joinUrl": f"{self.server.base_url}/join",  # type: ignore[attr-defined]
                    "categories": CATEGORIES,
                    "serverTime": now_iso(),
                }
            )
            return

        if path == "/api/responses":
            after = self._int_query(query, "after", 0)
            category = self._optional_category_query(query)
            highlight = None
            if query.get("highlight", [""])[0] in {"1", "true", "yes"}:
                highlight = True
            responses, latest_seq = self.store.get_responses(after, category, highlight)
            self._send_json(
                {
                    "responses": responses,
                    "latestSeq": latest_seq,
                    "categories": CATEGORIES,
                }
            )
            return

        if path == "/api/summary":
            self._send_json(self.store.summary())
            return

        if path == "/api/export.csv":
            category = self._optional_category_query(query)
            highlight = True if query.get("highlight", [""])[0] in {"1", "true", "yes"} else None
            responses, _ = self.store.get_responses(0, category, highlight)
            self._send_csv(responses)
            return

        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return

        if path in STATIC_FILES:
            filename, content_type = STATIC_FILES[path]
            self._send_file(BASE_DIR / filename, content_type)
            return

        if path in PAGE_PATHS or path == "/index.html":
            self._send_file(BASE_DIR / "index.html", "text/html; charset=utf-8")
            return

        self._send_json({"error": "找不到頁面。"}, HTTPStatus.NOT_FOUND)

    def _handle_post(self) -> None:
        path = urlparse(self.path).path
        payload = self._read_json()

        if path == "/api/join":
            nickname = clean_text(payload.get("nickname"), "參加者暱稱", 30)
            participant = self.store.create_participant(nickname)
            self._send_json({"participant": participant}, HTTPStatus.CREATED)
            return

        if path == "/api/responses":
            participant_id = payload.get("participantId")
            if not isinstance(participant_id, str) or not participant_id:
                raise ApiError("缺少參加者資料，請重新輸入暱稱。")
            category = clean_category(payload.get("category"))
            text = clean_text(payload.get("text"), "回答內容", 2000)
            response = self.store.add_response(participant_id, category, text)
            self._send_json({"response": response}, HTTPStatus.CREATED)
            return

        if path == "/api/finish":
            participant_id = payload.get("participantId")
            if not isinstance(participant_id, str) or not participant_id:
                raise ApiError("缺少參加者資料。")
            self.store.finish_participant(participant_id)
            self._send_json({"ok": True})
            return

        self._send_json({"error": "找不到 API。"}, HTTPStatus.NOT_FOUND)

    def _handle_patch(self) -> None:
        response_id = self._response_id_from_path()
        payload = self._read_json()
        if not isinstance(payload, dict) or ({"highlight", "adminNote"} & set(payload)) == set():
            raise ApiError("沒有可更新的欄位。")
        response = self.store.update_response(response_id, payload)
        self._send_json({"response": response})

    def _handle_delete(self) -> None:
        response_id = self._response_id_from_path()
        self.store.delete_response(response_id)
        self._send_json({"ok": True})

    def _response_id_from_path(self) -> str:
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/responses/([A-Za-z0-9_-]+)", path)
        if not match:
            raise ApiError("找不到這則回覆。", HTTPStatus.NOT_FOUND)
        return match.group(1)

    def _read_json(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError("請求格式不正確。") from None
        if length <= 0:
            raise ApiError("缺少請求內容。")
        if length > 1_000_000:
            raise ApiError("請求內容過大。", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError("請求格式不正確。") from None
        if not isinstance(payload, dict):
            raise ApiError("請求格式不正確。")
        return payload

    def _int_query(self, query: dict, name: str, default: int) -> int:
        raw = query.get(name, [""])[0]
        if raw == "":
            return default
        try:
            value = int(raw)
        except ValueError:
            raise ApiError(f"{name} 參數不正確。") from None
        return max(0, value)

    def _optional_category_query(self, query: dict) -> int | None:
        raw = query.get("category", [""])[0]
        if raw == "":
            return None
        return clean_category(raw)

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_csv(self, responses: list[dict]) -> None:
        buffer = io.StringIO()
        buffer.write("\ufeff")
        writer = csv.writer(buffer)
        writer.writerow(["序號", "參加者暱稱", "類別", "填寫內容", "收到時間", "精選", "後台備註"])
        for response in responses:
            category = int(response.get("category", 0))
            category_name = CATEGORIES[category - 1] if 1 <= category <= len(CATEGORIES) else ""
            writer.writerow(
                [
                    response.get("seq", ""),
                    response.get("nickname", ""),
                    category_name,
                    response.get("text", ""),
                    response.get("createdAt", ""),
                    "是" if response.get("highlight") else "",
                    response.get("adminNote", ""),
                ]
            )
        body = buffer.getvalue().encode("utf-8")
        filename = quote("回答資料.csv")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f"attachment; filename=\"responses.csv\"; filename*=UTF-8''{filename}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "找不到檔案。"}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    parser = argparse.ArgumentParser(description="八大智能數學邏輯組活動填答系統")
    parser.add_argument("--host", default="0.0.0.0", help="監聽位址，預設 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000, help="連接埠，預設 8000")
    parser.add_argument("--public-url", default=os.environ.get("PUBLIC_URL", ""), help="QR Code 要使用的對外網址")
    parser.add_argument("--data", default=str(BASE_DIR / "data" / "store.json"), help="資料檔路徑")
    args = parser.parse_args()

    store = Store(args.data)
    server = AppServer((args.host, args.port), Handler, store)
    actual_port = int(server.server_address[1])
    server.base_url = make_base_url(args.host, actual_port, args.public_url)

    print()
    print(TITLE)
    print(f"首頁：{server.base_url}/")
    print(f"掃碼參加：{server.base_url}/join")
    print(f"觀看答覆：{server.base_url}/watch")
    print(f"後台：{server.base_url}/admin")
    print(f"資料檔：{store.path}")
    print("按 Ctrl+C 停止伺服器。")
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n伺服器已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
