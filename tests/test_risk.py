"""單筆部位的風險尺度。

起因：出場規則七條全是「技術面相對位置」，沒有一條看得到成本價 ——
實測賠 40% 和不賺不賠會給出一模一樣的建議。而且進場端算出的停損價，
出場端從來不檢查。
"""

from __future__ import annotations

import math

import pytest

from config import rules as rules_cfg
from core import risk


def test_low_volatility_gets_tighter_stop_than_high():
    """核心修正：固定 8% 對低波動股等於 9 個標準差，形同沒有停損。"""
    low = risk.max_loss_pct(0.15)
    mid = risk.max_loss_pct(0.30)
    high = risk.max_loss_pct(0.60)
    assert low < mid < high or (low < mid and mid <= high)
    assert low < 8.0, "低波動股不該還是用 8% 停損"


def test_stop_is_monotonic_in_volatility():
    vols = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60]
    losses = [risk.max_loss_pct(v) for v in vols]
    assert losses == sorted(losses), losses


def test_bounds_are_respected():
    c = rules_cfg()["stop"]
    assert risk.max_loss_pct(0.01) == c["min_loss_pct"]     # 極低波動夾在下限
    assert risk.max_loss_pct(5.0) == c["max_loss_pct"]      # 極高波動夾在上限


def test_matches_sigma_formula_inside_the_bounds():
    c = rules_cfg()["stop"]
    ann = 0.30
    expected = c["vol_sigma"] * (ann / math.sqrt(risk.TRADING_DAYS)) * 100
    assert c["min_loss_pct"] < expected < c["max_loss_pct"]   # 確認沒被夾住
    assert risk.max_loss_pct(ann) == pytest.approx(expected)


def test_missing_volatility_falls_back_to_the_cap():
    """波動算不出來時要用最保守的上限，不能靜靜給一個樂觀的數字。"""
    cap = rules_cfg()["stop"]["max_loss_pct"]
    assert risk.max_loss_pct(None) == cap
    assert risk.max_loss_pct(0) == cap
    assert risk.max_loss_pct(-1) == cap


def test_stop_price_never_exceeds_the_loss_cap():
    """停損價只由虧損上限決定，不混進 20MA。

    混進去的話會自相矛盾：20MA 離現價 -15% 時，停損價會落在虧損上限
    之外，等於進場端說「停損 -15%」而出場端的 P0 在 -5.7% 就要求出場。
    """
    for close in (10.0, 100.0, 1000.0):
        for ann in (0.10, 0.30, 0.60, None):
            stop, loss = risk.stop_price(close=close, ann_vol=ann)
            assert stop == pytest.approx(close * (1 - loss / 100), abs=0.01)
            implied = (close - stop) / close * 100
            assert implied <= rules_cfg()["stop"]["max_loss_pct"] + 0.01, \
                f"停損距離 {implied:.2f}% 超過虧損上限"


def test_entry_stop_and_exit_p0_agree():
    """進場端算出的停損價，換算成虧損%，必須等於出場端 P0 的觸發門檻 ——
    這正是這次修正的目的：兩邊用同一把尺。"""
    close, ann = 100.0, 0.30
    stop, loss = risk.stop_price(close=close, ann_vol=ann)
    assert (close - stop) / close * 100 == pytest.approx(loss, abs=0.01)
    assert risk.max_loss_pct(ann) == pytest.approx(loss)


def test_thresholds_come_from_config():
    """鐵則 2：門檻要能從 config 調。"""
    cfg = rules_cfg()
    cfg["stop"] = {"vol_sigma": 1.0, "min_loss_pct": 0.5,
                   "max_loss_pct": 50.0, "severity": 4}
    loose = risk.max_loss_pct(0.30, cfg=cfg)
    assert loose == pytest.approx(risk.max_loss_pct(0.30) / 3, abs=0.01)
