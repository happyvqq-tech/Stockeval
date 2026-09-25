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


# ---------- P0 硬性停損 ----------
def _drifting_down(n=140):
    """緩跌：剛跌破 20MA，自 60 日高回撤只有 -12%，技術面「還好」。"""
    close = np.concatenate([np.full(80, 100.0), np.linspace(100, 88, 60)])
    return pd.DataFrame(
        {"open": close, "high": close * 1.005, "low": close * 0.995,
         "close": close, "volume": np.full(len(close), 10_000.0)},
        index=pd.bdate_range("2025-01-01", periods=len(close)))


def test_large_loss_no_longer_reads_the_same_as_break_even():
    """修正前實測：賠 40% 與不賺不賠給出一模一樣的建議，因為七條規則
    全是技術面相對位置，沒有一條看得到成本。"""
    m = compute(_drifting_down(), symbol="X", market="US")
    flat = evaluate(m, unrealized_pct=0.0)
    deep = evaluate(m, unrealized_pct=-40.0)
    assert flat["action"] != deep["action"], "虧損幅度必須影響建議"
    assert "P0" in deep["triggered"] and "P0" not in flat["triggered"]


def test_p0_alone_is_enough_to_demand_exit():
    """硬性停損的意義是出場，不是減碼 —— 單獨觸發就要到 EXIT_ALL。"""
    m = compute(_drifting_down(), symbol="US", market="US")
    out = evaluate(m, unrealized_pct=-40.0)
    assert out["action"] == "EXIT_ALL"


def test_p0_does_not_fire_when_flat_or_profitable():
    m = compute(_drifting_down(), symbol="X", market="US")
    for u in (0.0, 5.0, 50.0):
        assert "P0" not in evaluate(m, unrealized_pct=u)["triggered"]


def test_p0_threshold_scales_with_volatility():
    """低波動股的停損線要比高波動股緊。"""
    from core import risk
    calm, wild = dict(), dict()
    m = compute(_drifting_down(), symbol="X", market="US")
    calm, wild = dict(m), dict(m)
    calm["ann_vol"], wild["ann_vol"] = 0.12, 0.60

    loss_calm = risk.max_loss_pct(0.12)
    loss_wild = risk.max_loss_pct(0.60)
    assert loss_calm < loss_wild

    # 剛好落在兩者之間的虧損：低波動股該停損，高波動股還不用
    between = -(loss_calm + loss_wild) / 2
    assert "P0" in evaluate(calm, unrealized_pct=between)["triggered"]
    assert "P0" not in evaluate(wild, unrealized_pct=between)["triggered"]


def test_p0_explains_where_the_line_came_from():
    m = compute(_drifting_down(), symbol="X", market="US")
    out = evaluate(m, unrealized_pct=-40.0)
    p0 = next(r for r in out["rules"] if r["code"] == "P0")
    assert "停損線" in p0["detail"] and "年化波動" in p0["detail"]
    assert any("停損" in n for n in out["structural_notes"])


def test_p0_severity_is_config_driven():
    from config import rules as rules_cfg

    m = compute(_drifting_down(), symbol="X", market="US")
    cfg = rules_cfg()
    cfg["stop"]["severity"] = 1                       # 降級成「只是加重」
    out = evaluate(m, unrealized_pct=-40.0, cfg=cfg)
    assert "P0" in out["triggered"]
    assert out["action"] != "EXIT_ALL", "severity 調低後不該還是全數停損"


def test_entry_stop_price_is_what_p0_enforces():
    """端對端一致性：進場端給的停損價換算成虧損%，就是 P0 的觸發線。
    這正是修正前不成立的地方 —— 進場端說停損 139，出場端從來不檢查。"""
    from core.recommend import score

    m = compute(_drifting_down(), symbol="X", market="US")
    s = score(m)
    implied_loss = (s["close"] - s["stop"]) / s["close"] * 100

    just_inside = -(implied_loss - 0.5)
    just_outside = -(implied_loss + 0.5)
    assert "P0" not in evaluate(m, unrealized_pct=just_inside)["triggered"]
    assert "P0" in evaluate(m, unrealized_pct=just_outside)["triggered"]
