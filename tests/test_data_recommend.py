"""正規化層與推薦層測試。"""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from core.indicators import compute
from core.recommend import rank, score
from data.base import MarketAdapter, get_adapter, normalize
from data.cn import LIMIT_PCT, board_of


def raw(n=5):
    idx = ["2025-01-03", "2025-01-02", "2025-01-02", "2025-01-01", "2024-12-31"][:n]
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100},
        index=idx,
    )


def test_normalize_sorts_and_dedupes():
    out = normalize(raw(), market="TW", symbol="X")
    assert out.index.is_monotonic_increasing
    assert not out.index.has_duplicates
    assert isinstance(out.index, pd.DatetimeIndex)


def test_normalize_drops_halted_days():
    df = raw(3).copy()
    df.loc[df.index[0], "volume"] = 0          # 停牌日
    assert len(normalize(df, market="TW", symbol="X")) == 1  # 去重後剩 1 筆


def test_normalize_rejects_missing_columns():
    with pytest.raises(ValueError, match="缺少欄位"):
        normalize(pd.DataFrame({"close": [1.0]}), market="TW", symbol="X")


def test_normalize_empty_is_safe():
    assert normalize(pd.DataFrame(), market="TW", symbol="X").empty


def test_get_adapter_unknown_market():
    with pytest.raises(KeyError):
        get_adapter("JP")


def test_cn_board_limits():
    assert LIMIT_PCT[board_of("688111")] == 0.20     # 科創板
    assert LIMIT_PCT[board_of("300750")] == 0.20     # 創業板
    assert LIMIT_PCT[board_of("600519")] == 0.10     # 主板
    assert LIMIT_PCT[board_of("830799")] == 0.30     # 北交所


def uptrend(n=140):
    """穩定多頭：均線多頭排列、量價配合。"""
    close = 100 * np.exp(np.linspace(0, 0.35, n)) + np.sin(np.arange(n) / 7) * 0.3
    return pd.DataFrame(
        {"open": close * 0.998, "high": close * 1.005, "low": close * 0.995,
         "close": close, "volume": np.full(n, 10_000.0) * (1 + np.arange(n) / n)},
        index=pd.bdate_range("2025-01-01", periods=n),
    )


def downtrend(n=140):
    close = 100 * np.exp(np.linspace(0, -0.35, n))
    return pd.DataFrame(
        {"open": close * 1.002, "high": close * 1.005, "low": close * 0.995,
         "close": close, "volume": np.full(n, 10_000.0)},
        index=pd.bdate_range("2025-01-01", periods=n),
    )


def test_uptrend_scores_above_downtrend():
    up = score(compute(uptrend(), symbol="U", market="US"))
    dn = score(compute(downtrend(), symbol="D", market="US"))
    assert up["score"] > dn["score"]
    assert up["rating"] in ("STRONG_BUY", "BUY")
    assert dn["rating"] in ("REDUCE", "AVOID")


def test_cost_penalty_only_hits_high_cost_market():
    df = uptrend()
    tw = score(compute(df, symbol="U", market="TW"))
    us = score(compute(df, symbol="U", market="US"))
    assert us["cost_penalty"] == 0
    assert tw["cost_penalty"] > 0
    assert tw["score"] < us["score"]


def test_stop_below_entry_and_target_above():
    s = score(compute(uptrend(), symbol="U", market="US"))
    assert s["stop"] < s["entry"] < s["target"]
    assert s["risk_pct"] > 0


def test_rank_orders_by_score():
    ms = [compute(downtrend(), symbol="D", market="US"),
          compute(uptrend(), symbol="U", market="US")]
    out = rank(ms)
    assert [x["symbol"] for x in out] == ["U", "D"]
    assert rank(ms, top=1)[0]["symbol"] == "U"


def test_score_breakdown_sums_to_total():
    s = score(compute(uptrend(), symbol="U", market="TW"))
    total = sum(b["points"] for b in s["breakdown"]) - s["cost_penalty"]
    assert abs(total - s["score"]) < 0.51


def test_fresh_fetch_is_cropped_to_requested_range():
    """as_of 鐵則的最後一道防線：compute() 無條件把最後一列當成 as_of，
    如果 adapter 給的資料超出 [start, end]，一定要在這裡被裁掉，否則會
    偷看到未來資料而不自知 —— 對回測是致命的。"""

    class OverGenerousAdapter(MarketAdapter):
        market = "TW"

        def _fetch(self, symbol, start, end):
            # 故意回傳比要求範圍更寬的資料（模擬 adapter 沒有精準遵守
            # start/end，或資料源本身多給的情況）
            idx = pd.bdate_range("2025-01-01", periods=200)
            n = len(idx)
            return pd.DataFrame(
                {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                 "volume": [1000] * n},
                index=idx,
            )

    ad = OverGenerousAdapter()
    df = ad.ohlcv("2330", "2025-02-01", "2025-02-28", use_cache=False)
    assert df.index.min().date() >= date(2025, 2, 1)
    assert df.index.max().date() <= date(2025, 2, 28)


def test_cache_hit_and_fresh_fetch_crop_identically():
    """快取命中與第一次抓取兩條路徑，對同一個請求要裁出一樣的範圍 ——
    這正是這次修正要保證的一致性。"""
    from data import cache as cache_mod

    class Wide(MarketAdapter):
        market = "TW"

        def _fetch(self, symbol, start, end):
            idx = pd.bdate_range("2025-01-01", periods=200)
            return pd.DataFrame(
                {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                 "volume": [1000] * len(idx)}, index=idx,
            )

    ad = Wide()
    fresh = ad.ohlcv("2330", "2025-02-01", "2025-02-28", use_cache=True)
    assert cache_mod.load("TW", "2330") is not None       # 確認真的存進快取了

    cached = ad.ohlcv("2330", "2025-02-01", "2025-02-28", use_cache=True)
    assert ad.from_cache is True
    pd.testing.assert_frame_equal(fresh, cached, check_freq=False)
