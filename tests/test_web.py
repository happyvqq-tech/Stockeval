"""網站層測試。不打真實網路 —— 用假 adapter 換掉 web.logic.get_adapter。"""

from __future__ import annotations

import re
import time
import zlib

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from data.base import MarketAdapter
from web import auth
from web.app import app


class FakeAdapter(MarketAdapter):
    market = "TW"

    def __init__(self, *, fail_symbols=(), adjusted=True):
        self.fail_symbols = set(fail_symbols)
        self.adjusted = adjusted

    def _fetch(self, symbol, start, end):
        if symbol in self.fail_symbols:
            return pd.DataFrame()               # 空表 → 上層判定筆數不足
        # 用 crc32 而不是 hash()：Python 的字串 hash 每個行程都不同
        # （PYTHONHASHSEED 隨機化），會讓測試資料每次跑都變、造成偶發失敗。
        rng = np.random.default_rng(zlib.crc32(symbol.encode()))
        n = 120
        close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.012, n)))
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
        return pd.DataFrame(
            {"open": close, "high": close * 1.01, "low": close * 0.99,
             "close": close, "volume": rng.integers(10_000, 50_000, n)},
            index=idx,
        )

    def limit_pct(self, symbol):
        return 0.10


def _extract_job_id(html: str) -> str:
    # 用固定的 <!-- job:ID --> 標記，不用 /api/scan/ 這個字面路徑：
    # 背景執行緒可能在這個回應渲染完成前就跑完了（假資料算得很快），
    # 這時「掃描中」那個分支根本不會被渲染，但標記在三種狀態下都存在。
    m = re.search(r"<!-- job:([a-f0-9]+) -->", html)
    assert m, f"找不到 job id：{html[:300]}"
    return m.group(1)


def _poll_until_done(client, job_id: str, timeout=5.0) -> str:
    deadline = time.time() + timeout
    html = ""
    while time.time() < deadline:
        html = client.get(f"/api/scan/{job_id}").text
        if "掃描中" not in html:
            return html
        time.sleep(0.05)
    raise TimeoutError(f"掃描逾時未完成：{html[:300]}")


@pytest.fixture
def fake_adapter(monkeypatch):
    fake = FakeAdapter(fail_symbols={"BAD"})
    monkeypatch.setattr("web.logic.get_adapter", lambda market, **kw: fake)
    return fake


@pytest.fixture
def universe_dir(monkeypatch, tmp_path):
    monkeypatch.setattr("data.universe._DIR", tmp_path)
    (tmp_path / "TW.txt").write_text("2330 台積電\n2317 鴻海\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def local_mode(monkeypatch):
    """網站層測試跑在本機模式，存取控制本身另見 tests/test_auth.py。"""
    monkeypatch.delenv("STOCKCORE_PASSWORD", raising=False)
    monkeypatch.setenv("STOCKCORE_LOCAL_ONLY", "1")
    monkeypatch.setenv("STOCKCORE_RATE_LIMIT", "10000")
    auth.reset_rate_limit()


@pytest.fixture
def client(fake_adapter, universe_dir, local_mode):
    return TestClient(app)


# ---------- 頁面基本可用 ----------
def test_index_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200 and "推薦排序" in r.text


def test_check_form_loads(client):
    r = client.get("/check")
    assert r.status_code == 200 and "個股檢查" in r.text


def test_universe_page_lists_entries(client):
    r = client.get("/universe?market=TW")
    assert "2330" in r.text and "台積電" in r.text


# ---------- 掃描：完整生命週期 ----------
def test_scan_full_lifecycle(client):
    r = client.post("/api/scan", data={"market": "TW", "symbols": "2330,2317"})
    assert r.status_code == 200
    job_id = _extract_job_id(r.text)
    html = _poll_until_done(client, job_id)
    assert "2330" in html and "2317" in html
    assert "評級" in html


def test_scan_defaults_to_universe_when_no_symbols(client):
    r = client.post("/api/scan", data={"market": "TW", "symbols": ""})
    job_id = _extract_job_id(r.text)
    html = _poll_until_done(client, job_id)
    assert "2330" in html and "2317" in html          # 來自 universe_dir 種的股票池


def test_scan_single_failure_does_not_break_batch(client):
    """單一標的失敗不能拖垮整批 —— 這是 cli.py 就有的行為，網站層要維持一致。"""
    r = client.post("/api/scan", data={"market": "TW", "symbols": "2330,BAD"})
    job_id = _extract_job_id(r.text)
    html = _poll_until_done(client, job_id)
    assert "2330" in html
    assert "BAD" in html                               # 失敗清單要看得到


def test_scan_carries_as_of(client):
    r = client.post("/api/scan", data={"market": "TW", "symbols": "2330"})
    job_id = _extract_job_id(r.text)
    html = _poll_until_done(client, job_id)
    assert "as_of" in html


def test_scan_unknown_job_returns_friendly_error(client):
    r = client.get("/api/scan/does-not-exist")
    assert "找不到" in r.text


def test_scan_min_score_filters_results(client):
    r = client.post("/api/scan", data={"market": "TW", "symbols": "2330,2317", "min_score": "999"})
    job_id = _extract_job_id(r.text)
    html = _poll_until_done(client, job_id)
    assert "沒有標的通過篩選條件" in html


def test_scan_flags_unadjusted_data(monkeypatch, universe_dir, local_mode):
    fake = FakeAdapter(adjusted=False)
    monkeypatch.setattr("web.logic.get_adapter", lambda market, **kw: fake)
    client = TestClient(app)
    r = client.post("/api/scan", data={"market": "TW", "symbols": "2330"})
    job_id = _extract_job_id(r.text)
    html = _poll_until_done(client, job_id)
    assert "未還原權息" in html


def test_scan_rejects_concurrent_scans(client):
    """同一時間只准一個掃描在跑，避免疊加打 API。"""
    from web import jobs

    slow = jobs.create("TW", ["2330"])
    jobs._scan_slot.acquire()                          # 模擬有個掃描正在進行
    try:
        ok = jobs.start(slow, top=None, min_score=0.0)
        assert ok is False
        assert slow.status == "error"
    finally:
        jobs._scan_slot.release()


# ---------- 個股檢查 ----------
def test_check_page_shows_as_of_and_all_rules(client):
    r = client.post("/check", data={"market": "TW", "symbol": "2330", "unrealized": "25"})
    assert r.status_code == 200
    assert "as_of" in r.text
    for code in ["P6", "P7", "P8", "P10", "P11", "P13", "V6-2"]:
        assert code in r.text


def test_check_page_shows_error_on_bad_symbol(client):
    r = client.post("/check", data={"market": "TW", "symbol": "BAD", "unrealized": "0"})
    assert r.status_code == 200
    assert "不足 60 筆" in r.text


def test_check_page_shows_structural_notes(client):
    """structural_notes 不准被折疊或省略 —— 那裡面是會讓人賠錢的資訊。"""
    r = client.post("/check", data={"market": "CN", "symbol": "2330", "unrealized": "25"})
    assert "T+1" in r.text          # A 股一定會帶出 T+1 提示，不依賴隨機資料


# ---------- 股票池編輯 ----------
def test_universe_add_writes_file(client, universe_dir):
    r = client.post("/universe/add", data={"market": "TW", "code": "2454", "name": "聯發科"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "2454" in (universe_dir / "TW.txt").read_text(encoding="utf-8")


def test_universe_add_duplicate_is_rejected(client, universe_dir):
    client.post("/universe/add", data={"market": "TW", "code": "9999", "name": "測試"})
    client.post("/universe/add", data={"market": "TW", "code": "9999", "name": "重複"})
    content = (universe_dir / "TW.txt").read_text(encoding="utf-8")
    assert content.count("9999") == 1


def test_universe_remove_writes_file(client, universe_dir):
    client.post("/universe/remove", data={"market": "TW", "code": "2317"})
    assert "2317" not in (universe_dir / "TW.txt").read_text(encoding="utf-8")


def test_universe_edit_creates_backup(client, universe_dir):
    client.post("/universe/add", data={"market": "TW", "code": "3008", "name": "大立光"})
    assert (universe_dir / "TW.txt.bak").exists()


def test_universe_add_empty_code_rejected(client, universe_dir):
    r = client.post("/universe/add", data={"market": "TW", "code": "  ", "name": "x"},
                     follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_every_route_is_behind_access_control(universe_dir, monkeypatch):
    """所有實際頁面都必須受保護 —— 只有 /healthz 可以例外。"""
    monkeypatch.delenv("STOCKCORE_PASSWORD", raising=False)
    monkeypatch.delenv("STOCKCORE_LOCAL_ONLY", raising=False)
    auth.reset_rate_limit()
    c = TestClient(app)

    for path in ["/", "/check", "/universe", "/review"]:
        assert c.get(path).status_code == 503, f"{path} 沒有被存取控制擋住"
    assert c.post("/api/scan", data={"market": "TW", "symbols": "2330"}).status_code == 503
    assert c.get("/api/scan/whatever").status_code == 503
    assert c.post("/api/review", data={"market": "TW", "asof": "2025-01-01"}).status_code == 503
    assert c.get("/api/review/whatever").status_code == 503
    assert c.post("/universe/add", data={"market": "TW", "code": "1", "name": "x"}).status_code == 503
    assert c.post("/universe/remove", data={"market": "TW", "code": "1"}).status_code == 503
    assert c.get("/healthz").status_code == 200


# ---------- 復盤 ----------
class ReviewFakeAdapter(MarketAdapter):
    """asof 之前走勢平緩、之後明顯分歧的假時間軸，讓「高分表現較好」的
    摘要統計有東西可以驗證，而不是隨機資料湊巧算出來的。"""

    market = "TW"

    _ASOF = pd.Timestamp("2025-01-01") + pd.tseries.offsets.BDay(149)
    _UNTIL = pd.Timestamp("2025-01-01") + pd.tseries.offsets.BDay(209)

    def __init__(self):
        rng_up = np.random.default_rng(11)
        before_up = 100 * np.exp(np.cumsum(rng_up.normal(0.003, 0.01, 150)))
        after_up = before_up[-1] * np.exp(np.cumsum(rng_up.normal(0.002, 0.01, 60)))

        rng_dn = np.random.default_rng(12)
        before_dn = 100 * np.exp(np.cumsum(rng_dn.normal(-0.003, 0.01, 150)))
        after_dn = before_dn[-1] * np.exp(np.cumsum(rng_dn.normal(-0.002, 0.01, 60)))

        idx = pd.bdate_range("2025-01-01", periods=210)

        def frame(closes):
            c = np.asarray(closes)
            return pd.DataFrame(
                {"open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
                 "volume": np.full(len(c), 10_000.0)}, index=idx)

        self.timelines = {
            "UP": frame(np.concatenate([before_up, after_up])),
            "DOWN": frame(np.concatenate([before_dn, after_dn])),
        }

    def _fetch(self, symbol, start, end):
        full = self.timelines.get(symbol)
        if full is None:
            return pd.DataFrame()
        return full.loc[str(start):str(end)]

    def limit_pct(self, symbol):
        return None


@pytest.fixture
def review_fake_adapter(monkeypatch):
    fake = ReviewFakeAdapter()
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: fake)
    return fake


def test_review_form_loads(client):
    r = client.get("/review")
    assert r.status_code == 200 and "復盤" in r.text


def test_review_full_lifecycle(review_fake_adapter, client):
    asof = ReviewFakeAdapter._ASOF.date().isoformat()
    until = ReviewFakeAdapter._UNTIL.date().isoformat()
    r = client.post("/api/review", data={
        "market": "TW", "symbols": "UP,DOWN", "asof": asof, "until": until})
    job_id = _extract_review_job_id(r.text)
    html = _poll_review_until_done(client, job_id)
    assert "UP" in html and "DOWN" in html
    assert asof in html and until in html


def test_review_defaults_to_universe(review_fake_adapter, universe_dir, local_mode):
    (universe_dir / "TW.txt").write_text("UP 上漲股\nDOWN 下跌股\n", encoding="utf-8")
    client = TestClient(app)
    asof = ReviewFakeAdapter._ASOF.date().isoformat()
    r = client.post("/api/review", data={"market": "TW", "asof": asof})
    job_id = _extract_review_job_id(r.text)
    html = _poll_review_until_done(client, job_id)
    assert "UP" in html and "DOWN" in html


def test_review_single_failure_does_not_break_batch(review_fake_adapter, client):
    asof = ReviewFakeAdapter._ASOF.date().isoformat()
    until = ReviewFakeAdapter._UNTIL.date().isoformat()
    r = client.post("/api/review", data={
        "market": "TW", "symbols": "UP,NOT_LISTED", "asof": asof, "until": until})
    job_id = _extract_review_job_id(r.text)
    html = _poll_review_until_done(client, job_id)
    assert "UP" in html
    assert "NOT_LISTED" in html


def test_review_unknown_job_returns_friendly_error(client):
    assert "找不到" in client.get("/api/review/does-not-exist").text


def _extract_review_job_id(html: str) -> str:
    m = re.search(r"<!-- job:([a-f0-9]+) -->", html)
    assert m, f"找不到 job id：{html[:300]}"
    return m.group(1)


def _poll_review_until_done(client, job_id: str, timeout=5.0) -> str:
    deadline = time.time() + timeout
    html = ""
    while time.time() < deadline:
        html = client.get(f"/api/review/{job_id}").text
        if "復盤中" not in html:
            return html
        time.sleep(0.05)
    raise TimeoutError(f"復盤逾時未完成：{html[:300]}")
