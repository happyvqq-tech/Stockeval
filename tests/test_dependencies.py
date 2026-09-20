"""相依套件缺失時的錯誤處理。

起因：部署版為了縮小映像檔把 akshare 拿掉，但介面仍然讓使用者選 CN，
結果網頁上直接顯示 "No module named 'akshare'" —— 對使用者毫無意義。
"""

from __future__ import annotations

import builtins

import pytest

from data.base import MissingDependency


@pytest.fixture
def no_module(monkeypatch):
    """讓指定的套件在 import 時看起來像沒安裝。"""
    def _block(name: str):
        real_import = builtins.__import__

        def fake_import(mod, *args, **kwargs):
            if mod == name or mod.startswith(name + "."):
                raise ImportError(f"No module named '{name}'")
            return real_import(mod, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
    return _block


def test_missing_akshare_gives_actionable_message(no_module):
    from data.cn import ChinaAdapter

    no_module("akshare")
    with pytest.raises(MissingDependency) as e:
        ChinaAdapter().ohlcv("000636", "2025-01-01", use_cache=False)

    msg = str(e.value)
    assert "akshare" in msg
    assert "pip install akshare" in msg          # 本機怎麼修
    assert "requirements-web.txt" in msg          # 部署版怎麼修
    assert "No module named" not in msg           # 不該把原始訊息丟給使用者


def test_missing_yfinance_gives_actionable_message(no_module):
    from data.us import USAdapter

    no_module("yfinance")
    with pytest.raises(MissingDependency) as e:
        USAdapter().ohlcv("AAPL", "2025-01-01", use_cache=False)

    msg = str(e.value)
    assert "pip install yfinance" in msg


def test_missing_dependency_is_not_swallowed_as_generic_failure(no_module):
    """MissingDependency 要能被上層辨識出來，不能被當成一般的「這檔抓不到」。"""
    from data.cn import ChinaAdapter

    no_module("akshare")
    with pytest.raises(MissingDependency):
        ChinaAdapter().ohlcv("000636", "2025-01-01", use_cache=False)


def test_web_check_page_shows_friendly_message(monkeypatch):
    """網頁上要看到可行動的說明，而不是原始的 ModuleNotFoundError。"""
    from fastapi.testclient import TestClient

    from web import auth
    from web.app import app

    monkeypatch.delenv("STOCKCORE_PASSWORD", raising=False)
    monkeypatch.setenv("STOCKCORE_LOCAL_ONLY", "1")
    monkeypatch.setenv("STOCKCORE_RATE_LIMIT", "10000")
    auth.reset_rate_limit()

    def boom(market, **kw):
        raise MissingDependency("A 股資料需要 akshare 套件，這個環境沒有安裝。\n"
                                "本機：pip install akshare")

    monkeypatch.setattr("web.logic.get_adapter", boom)
    r = TestClient(app).post("/check", data={"market": "CN", "symbol": "000636",
                                             "unrealized": "0"})
    assert r.status_code == 200
    assert "pip install akshare" in r.text
    assert "whitespace-pre-line" in r.text        # 多行訊息要能換行顯示
