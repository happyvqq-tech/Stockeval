"""規則層測試。重點是「市場差異是否真的只由 config 驅動」。"""

import numpy as np
import pandas as pd
import pytest

from config import market_cfg
from core.indicators import compute, is_limit_down
from core.rules import evaluate


def series(closes, *, vol=None, opens=None):
    """把一串收盤價包成統一格式的 DataFrame。"""
    n = len(closes)
    c = np.asarray(closes, dtype=float)
    o = np.asarray(opens, dtype=float) if opens is not None else c
    v = np.asarray(vol, dtype=float) if vol is not None else np.full(n, 10_000.0)
    return pd.DataFrame(
        {"open": o, "high": np.maximum(c, o) * 1.001,
         "low": np.minimum(c, o) * 0.999, "close": c, "volume": v},
        index=pd.bdate_range("2025-01-01", periods=n),
    )


def p6_case():
    """已在 20MA 下方數日，今日低量黑K再破位 —— P6 是唯一的邊際規則。"""
    base = list(np.linspace(100, 104, 120))      # 緩漲，回撤不會觸發 V6-2
    tail = [103.0, 101.5, 100.6, 99.8, 99.0]     # 連續收在 20MA 之下
    closes = base + tail + [96.2]                # 今日 -2.8%
    vol = [10_000.0] * len(closes)
    vol[-1] = 8_000.0                            # 量比 < 1.3：美股濾掉，台股不濾
    return series(closes, vol=vol)


@pytest.fixture(scope="module")
def p6_metrics():
    df = p6_case()
    return {mkt: compute(df, symbol="T", market=mkt) for mkt in ("TW", "US", "CN")}


def test_p6_fires_without_volume_confirmation(p6_metrics):
    assert evaluate(p6_metrics["TW"])["triggered"].count("P6") == 1
    assert evaluate(p6_metrics["CN"])["triggered"].count("P6") == 1


def test_p6_filtered_in_us_by_volume(p6_metrics):
    out = evaluate(p6_metrics["US"])
    assert "P6" not in out["triggered"]
    p6 = next(r for r in out["rules"] if r["code"] == "P6")
    assert "量比" in p6["detail"]


def test_same_data_different_action_across_markets(p6_metrics):
    """同一組資料：台股/A股全數停損，美股因 P6 被濾掉而只降至 50%。"""
    assert evaluate(p6_metrics["TW"])["action"] == "EXIT_ALL"
    assert evaluate(p6_metrics["CN"])["action"] == "EXIT_ALL"
    assert evaluate(p6_metrics["US"])["action"] == "REDUCE_50"


def test_cn_emits_t1_note(p6_metrics):
    notes = " ".join(evaluate(p6_metrics["CN"])["structural_notes"])
    assert "T+1" in notes
    tw_notes = " ".join(evaluate(p6_metrics["TW"])["structural_notes"])
    assert "T+1" not in tw_notes


def test_cost_bps_per_market():
    assert market_cfg("TW")["cost_bps"] == 58.5
    assert market_cfg("CN")["cost_bps"] == 10
    assert market_cfg("US")["cost_bps"] == 0


def test_trailing_stop_needs_profit():
    df = p6_case()
    m = compute(df, symbol="T", market="TW")
    assert "P10" not in evaluate(m, unrealized_pct=0)["triggered"]
    assert "P10" in evaluate(m, unrealized_pct=25)["triggered"]


def test_limit_down_flags_stop_as_ineffective():
    m = compute(p6_case(), symbol="T", market="CN")
    out = evaluate(m, unrealized_pct=0, limit_down=True)
    assert any("跌停" in n for n in out["structural_notes"])


def test_unadjusted_data_is_flagged():
    m = compute(p6_case(), symbol="T", market="TW")
    out = evaluate(m, unrealized_pct=0, adjusted=False)
    assert any("還原權息" in n for n in out["structural_notes"])


def test_flat_ma20_suppresses_p6():
    """盤整帶的黑K破位不算破位（20MA 斜率濾網）。"""
    closes = [100 + 0.4 * np.sin(i / 3) for i in range(130)]
    closes[-1] = closes[-2] * 0.97
    m = compute(series(closes), symbol="T", market="TW")
    out = evaluate(m)
    p6 = next(r for r in out["rules"] if r["code"] == "P6")
    assert not p6["triggered"]


def test_is_limit_down():
    df = series([100.0, 90.0])
    assert is_limit_down(df, 0.10)
    assert not is_limit_down(series([100.0, 95.0]), 0.10)


def test_compute_rejects_short_history():
    with pytest.raises(ValueError, match="60"):
        compute(series(list(range(100, 130))), symbol="T", market="TW")


@pytest.mark.parametrize("market", ["TW", "US", "CN"])
def test_untriggered_rules_do_not_claim_threshold_met(market):
    """送給 LLM 的 detail 不能自相矛盾：沒觸發就不該寫「≥ 門檻」。"""
    m = compute(p6_case(), symbol="T", market=market)
    for r in evaluate(m, unrealized_pct=0)["rules"]:
        if not r["triggered"]:
            assert "≥" not in r["detail"], f"{r['code']}: {r['detail']}"
