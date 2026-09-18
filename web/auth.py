"""存取控制 —— 部署到公開網址時擋住不該進來的人。

設計原則是 fail closed：**沒設定密碼就拒絕對外服務**，而不是預設開放。
公開網址上一個沒有保護的服務，等於把你的 FinMind token 額度和伺服器
資源送給任何掃到這個網址的人。

三種狀態：
  1. STOCKCORE_PASSWORD 有設（且夠長） → 要求 HTTP Basic 驗證
  2. 沒設，但 STOCKCORE_LOCAL_ONLY=1   → 放行（python -m web 本機模式）
  3. 都沒有                             → 每個請求都回 503，並說明怎麼設定

HTTP Basic 的密碼是明文傳輸，所以只有在 HTTPS 底下才安全。
主流 PaaS（Render / Railway / Fly 等）預設就給 HTTPS，直接用沒問題；
自架反向代理的話請自己確認憑證有裝好。
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse, Response

MIN_PASSWORD_LEN = 8

# 健康檢查要讓平台探得到，否則部署會被判定為失敗。
# 這個端點不吐任何資料，所以免驗證是安全的。
EXEMPT_PATHS = {"/healthz"}

_NO_PASSWORD_MSG = (
    "這個服務尚未設定存取密碼，已拒絕對外提供服務。\n\n"
    "部署到公開網址時，請在平台的環境變數（Environment / Secrets）設定：\n"
    f"  STOCKCORE_PASSWORD=<至少 {MIN_PASSWORD_LEN} 個字元的密碼>\n\n"
    "在自己電腦上跑的話，請用 python -m web 啟動（會自動帶 STOCKCORE_LOCAL_ONLY=1）。\n"
)


def configured_password() -> str:
    """回傳有效的密碼；沒設或太短都當成沒設。"""
    pw = os.getenv("STOCKCORE_PASSWORD", "")
    return pw if len(pw) >= MIN_PASSWORD_LEN else ""


def local_only() -> bool:
    return os.getenv("STOCKCORE_LOCAL_ONLY", "") == "1"


def _password_matches(auth_header: str, expected: str) -> bool:
    """驗 HTTP Basic。帳號不檢查，只比對密碼。

    用 hmac.compare_digest 而不是 ==，避免用回應時間猜出密碼。
    """
    scheme, _, encoded = auth_header.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    _, sep, supplied = decoded.partition(":")
    if not sep:
        return False
    return hmac.compare_digest(supplied, expected)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        expected = configured_password()
        if not expected:
            if local_only():
                return await call_next(request)
            return PlainTextResponse(_NO_PASSWORD_MSG, status_code=503)

        if not _password_matches(request.headers.get("authorization", ""), expected):
            return Response(
                content="需要登入",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="stockcore"'},
            )
        return await call_next(request)


# --------------------------------------------------------------------------
# 速率限制：網址萬一外流，至少不會被人狂打而燒光 FinMind 額度。
# 單一使用者自用，門檻設寬鬆即可。
_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()
WINDOW_SECONDS = 60.0


def rate_limit() -> int:
    return int(os.getenv("STOCKCORE_RATE_LIMIT", "60"))


def _client_key(request) -> str:
    """取用戶端識別。X-Forwarded-For 可偽造，但用於速率限制夠了。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        limit = rate_limit()
        key = _client_key(request)
        now = time.time()

        with _hits_lock:
            recent = [t for t in _hits.get(key, []) if now - t < WINDOW_SECONDS]
            if len(recent) >= limit:
                _hits[key] = recent
                retry = int(WINDOW_SECONDS - (now - recent[0])) + 1
                return PlainTextResponse(
                    f"請求過於頻繁，請 {retry} 秒後再試。",
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                )
            recent.append(now)
            _hits[key] = recent

        return await call_next(request)


def reset_rate_limit() -> None:
    """測試用：清掉累積的計數。"""
    with _hits_lock:
        _hits.clear()
