"""存取控制測試。

部署到公開網址後，這層是唯一擋在你的資料和整個網際網路之間的東西，
所以每一種狀態都要有測試釘住 —— 特別是「沒設密碼要拒絕服務」。
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from web import auth
from web.app import app


def basic(password: str, user: str = "stockcore") -> dict:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("STOCKCORE_PASSWORD", raising=False)
    monkeypatch.delenv("STOCKCORE_LOCAL_ONLY", raising=False)
    monkeypatch.delenv("STOCKCORE_RATE_LIMIT", raising=False)
    auth.reset_rate_limit()


@pytest.fixture
def client():
    return TestClient(app)


# ---------- 沒設密碼：必須整個拒絕，不能預設開放 ----------
def test_refuses_service_when_no_password_and_not_local(client):
    r = client.get("/")
    assert r.status_code == 503
    assert "STOCKCORE_PASSWORD" in r.text


def test_refusal_covers_every_page(client):
    for path in ["/", "/check", "/universe"]:
        assert client.get(path).status_code == 503
    assert client.post("/api/scan", data={"market": "TW"}).status_code == 503


def test_short_password_counts_as_unset(client, monkeypatch):
    """太短的密碼視同沒設 —— 公開網址上「1234」等於沒有保護。"""
    monkeypatch.setenv("STOCKCORE_PASSWORD", "1234")
    r = client.get("/")
    assert r.status_code == 503


# ---------- 本機模式：免密碼放行 ----------
def test_local_only_mode_allows_access(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_LOCAL_ONLY", "1")
    assert client.get("/").status_code == 200


# ---------- 有設密碼：要求驗證 ----------
def test_password_required(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_PASSWORD", "a-long-enough-password")
    r = client.get("/")
    assert r.status_code == 401
    assert "Basic" in r.headers.get("www-authenticate", "")


def test_correct_password_grants_access(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_PASSWORD", "a-long-enough-password")
    r = client.get("/", headers=basic("a-long-enough-password"))
    assert r.status_code == 200
    assert "推薦排序" in r.text


def test_wrong_password_rejected(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_PASSWORD", "a-long-enough-password")
    assert client.get("/", headers=basic("wrong-password-here")).status_code == 401


def test_username_is_ignored_only_password_matters(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_PASSWORD", "a-long-enough-password")
    r = client.get("/", headers=basic("a-long-enough-password", user="whoever"))
    assert r.status_code == 200


@pytest.mark.parametrize("header", [
    "",
    "Bearer sometoken",
    "Basic",
    "Basic !!!not-base64!!!",
    "Basic " + base64.b64encode(b"no-colon-here").decode(),
])
def test_malformed_auth_headers_rejected(client, monkeypatch, header):
    monkeypatch.setenv("STOCKCORE_PASSWORD", "a-long-enough-password")
    r = client.get("/", headers={"Authorization": header} if header else {})
    assert r.status_code == 401


def test_password_is_not_leaked_in_any_response(client, monkeypatch):
    secret = "super-secret-password"
    monkeypatch.setenv("STOCKCORE_PASSWORD", secret)
    for r in [client.get("/"), client.get("/", headers=basic("wrong-one-here")),
              client.get("/healthz")]:
        assert secret not in r.text


# ---------- 健康檢查免驗證，否則平台會判定部署失敗 ----------
def test_healthz_exempt_from_auth(client, monkeypatch):
    assert client.get("/healthz").status_code == 200          # 沒密碼也要通
    monkeypatch.setenv("STOCKCORE_PASSWORD", "a-long-enough-password")
    assert client.get("/healthz").status_code == 200          # 有密碼也免驗證


# ---------- 速率限制 ----------
def test_rate_limit_blocks_flood(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_LOCAL_ONLY", "1")
    monkeypatch.setenv("STOCKCORE_RATE_LIMIT", "5")
    codes = [client.get("/").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5
    assert 429 in codes
    assert "Retry-After" in client.get("/").headers


def test_rate_limit_is_per_client(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_LOCAL_ONLY", "1")
    monkeypatch.setenv("STOCKCORE_RATE_LIMIT", "3")
    for _ in range(3):
        client.get("/", headers={"X-Forwarded-For": "1.1.1.1"})
    assert client.get("/", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 429
    assert client.get("/", headers={"X-Forwarded-For": "2.2.2.2"}).status_code == 200


def test_rate_limit_applies_before_auth(client, monkeypatch):
    """不能讓人靠狂送錯密碼來耗資源。"""
    monkeypatch.setenv("STOCKCORE_PASSWORD", "a-long-enough-password")
    monkeypatch.setenv("STOCKCORE_RATE_LIMIT", "3")
    codes = [client.get("/", headers=basic("wrong")).status_code for _ in range(6)]
    assert 429 in codes


def test_healthz_not_rate_limited(client, monkeypatch):
    monkeypatch.setenv("STOCKCORE_LOCAL_ONLY", "1")
    monkeypatch.setenv("STOCKCORE_RATE_LIMIT", "2")
    assert all(client.get("/healthz").status_code == 200 for _ in range(10))
