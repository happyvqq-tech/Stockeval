"""正規化層與推薦層測試。"""

import numpy as np
import pandas as pd
import pytest

from core.indicators import compute
from core.recommend import rank, score
from data.base import get_adapter, normalize
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
