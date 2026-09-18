"""快取層與股票池測試 —— 全部離線，用假的 adapter。"""

from datetime import date

import pandas as pd
import pytest

from data import cache, universe
from data.base import MarketAdapter


class FakeAdapter(MarketAdapter):
    """記錄被呼叫幾次，用來證明快取真的省掉了網路請求。"""

    market = "TW"

    def __init__(self):
        self.calls = 0

    def _fetch(self, symbol, start, end):
        self.calls += 1
        idx = pd.bdate_range("2025-01-01", periods=80)
        return pd.DataFrame(
            {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 1000},
            index=idx,
        )


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKCORE_CACHE", str(tmp_path))
    monkeypatch.delenv("STOCKCORE_NO_CACHE", raising=False)
    return tmp_path


def test_second_call_hits_cache(tmp_cache):
    ad = FakeAdapter()
    ad.ohlcv("2330", "2025-01-01")
    assert ad.calls == 1 and ad.from_cache is False

    ad.ohlcv("2330", "2025-01-01")
    assert ad.calls == 1                  # 沒有再打一次 API
    assert ad.from_cache is True


def test_no_cache_flag_forces_refetch(tmp_cache):
    ad = FakeAdapter()
    ad.ohlcv("2330", "2025-01-01")
    ad.ohlcv("2330", "2025-01-01", use_cache=False)
    assert ad.calls == 2


def test_env_var_disables_cache(tmp_cache, monkeypatch):
    monkeypatch.setenv("STOCKCORE_NO_CACHE", "1")
    ad = FakeAdapter()
    ad.ohlcv("2330", "2025-01-01")
    ad.ohlcv("2330", "2025-01-01")
    assert ad.calls == 2


def test_stale_cache_is_refetched(tmp_cache, monkeypatch):
    ad = FakeAdapter()
    ad.ohlcv("2330", "2025-01-01")
    monkeypatch.setattr(cache, "age_hours", lambda m, s: 999.0)
    ad.ohlcv("2330", "2025-01-01")
    assert ad.calls == 2


def test_cache_miss_when_range_not_covered(tmp_cache):
    ad = FakeAdapter()
    ad.ohlcv("2330", "2025-01-01")
    ad.ohlcv("2330", "2020-01-01")        # 要更早的資料，快取不夠用
    assert ad.calls == 2


def test_cached_data_matches_fresh_data(tmp_cache):
    ad = FakeAdapter()
    fresh = ad.ohlcv("2330", "2025-01-01")
    cached = ad.ohlcv("2330", "2025-01-01")
    pd.testing.assert_frame_equal(fresh, cached, check_freq=False)


def test_corrupt_cache_falls_back_to_fetch(tmp_cache):
    ad = FakeAdapter()
    ad.ohlcv("2330", "2025-01-01")
    cache.path("TW", "2330").write_text("這不是 CSV\x00亂碼", encoding="utf-8")
    assert len(ad.ohlcv("2330", "2025-01-01")) == 80
    assert ad.calls == 2


def test_symbol_with_dot_is_safe_filename(tmp_cache):
    assert "." not in cache.path("US", "BRK.B").stem


def test_clear_removes_files(tmp_cache):
    ad = FakeAdapter()
    ad.ohlcv("2330", "2025-01-01")
    assert cache.clear("TW") == 1
    assert cache.load("TW", "2330") is None


def test_usable_returns_none_when_absent(tmp_cache):
    assert cache.usable("TW", "9999", date(2025, 1, 1), 12) is None


# ---------- 股票池 ----------
@pytest.mark.parametrize("market", ["TW", "US", "CN"])
def test_universe_loads(market):
    syms = universe.symbols(market)
    assert len(syms) >= 25
    assert len(syms) == len(set(syms))          # 不准重複
    assert all(s and " " not in s for s in syms)
    assert all(universe.names(market)[s] for s in syms)   # 每檔都要有名稱


def test_universe_unknown_market():
    with pytest.raises(FileNotFoundError, match="股票池"):
        universe.load("JP")


def test_cache_hits_when_start_falls_on_a_non_trading_day(tmp_cache):
    """回歸測試：請求起始日落在週末時，實際第一筆一定比它晚。
    若拿「資料實際第一筆」去比對，快取會永遠判定為不足而每次重抓。"""
    ad = FakeAdapter()                       # 假資料從 2025-01-01（週三）開始
    ad.ohlcv("2330", "2024-12-28")           # 週六，市場沒開
    assert ad.calls == 1

    ad.ohlcv("2330", "2024-12-28")
    assert ad.calls == 1, "請求日落在非交易日時快取失效"
    assert ad.from_cache is True


def test_cache_still_refetches_for_genuinely_earlier_start(tmp_cache):
    """但真的要更早的資料時，還是必須重抓 —— 不能為了命中率而放水。"""
    ad = FakeAdapter()
    ad.ohlcv("2330", "2024-12-28")
    ad.ohlcv("2330", "2020-01-01")
    assert ad.calls == 2


def test_meta_records_requested_range(tmp_cache):
    ad = FakeAdapter()
    ad.ohlcv("2330", "2024-12-28")
    meta = cache.read_meta("TW", "2330")
    assert meta["requested_start"] == "2024-12-28"
    assert meta["rows"] == 80
    assert meta["first_bar"] == "2025-01-01"


# ---------- CLI 顯示 ----------
def test_cjk_display_width():
    """中文全形佔兩欄，用 len() 對齊表格會歪掉。"""
    import cli
    assert cli._w("台積電") == 6
    assert cli._w("AAPL") == 4
    assert cli._w("強烈推薦") == 8
    for text in ("台積電", "AAPL", "", "聯發科MTK"):
        assert cli._w(cli._pad(text, 12)) == 12
        assert cli._w(cli._rpad(text, 12)) == 12


def test_pad_does_not_truncate_overlong_text():
    import cli
    assert "台積電" in cli._pad("台積電", 2)
