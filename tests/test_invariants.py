"""鐵則守門測試 —— 這些不是功能測試，是防止架構被改壞的警報器。

對應 CLAUDE.md 的五條鐵則。
"""

import ast
from pathlib import Path

import pytest

from config import rules as rules_cfg
from core.indicators import compute
from core.recommend import rank, score
from core.rules import evaluate
from tests.test_data_recommend import uptrend
from tests.test_rules import p6_case, series

CORE = Path(__file__).resolve().parents[1] / "core"
MARKET_CODES = {"TW", "US", "CN"}


# ---------- 鐵則 1：core/ 不准出現市場數值分支 ----------
def test_core_has_no_market_branches():
    offenders = []
    for f in CORE.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            parts = [node.left, *node.comparators]
            if any(isinstance(p, ast.Constant) and p.value in MARKET_CODES for p in parts):
                offenders.append(f"{f.name}:{node.lineno}")
    assert not offenders, f"core/ 出現市場分支（違反鐵則 1）：{offenders}"


# ---------- 鐵則 2：門檻改 config 就要生效，不准寫死 ----------
def test_black_k_threshold_is_config_driven(monkeypatch):
    """p6.drop_pct 是 indicators 與 rules 的唯一真相來源。"""
    df = p6_case()
    assert compute(df, symbol="T", market="TW")["black_k_break"] is True

    loose = rules_cfg()
    loose["p6"]["drop_pct"] = -20.0          # 幾乎不可能達到的跌幅
    monkeypatch.setattr("core.indicators.rules_cfg", lambda: loose)
    assert compute(df, symbol="T", market="TW")["black_k_break"] is False


def test_min_bars_is_config_driven(monkeypatch):
    short = series(list(range(100, 150)))    # 50 筆
    with pytest.raises(ValueError, match="60"):
        compute(short, symbol="T", market="TW")

    monkeypatch.setattr("core.indicators.market_cfg", lambda mkt: {"min_bars": 40})
    compute(short, symbol="T", market="TW")  # 門檻降到 40 就該放行


def test_rule_thresholds_are_config_driven():
    m = compute(p6_case(), symbol="T", market="TW")
    assert "P8" in evaluate(m)["triggered"]

    cfg = rules_cfg()
    cfg["p8"]["days_below"] = 999
    assert "P8" not in evaluate(m, cfg=cfg)["triggered"]


def test_score_weights_are_config_driven():
    m = compute(uptrend(), symbol="T", market="TW")   # 多頭資料，趨勢分才拿得到
    cfg = rules_cfg()
    base = score(m, cfg=cfg)["score"]

    cfg["score"]["trend"] = 0                # 拿掉趨勢權重，分數必須下降
    assert score(m, cfg=cfg)["score"] < base


# ---------- 鐵則 5：每個輸出都要帶 as_of ----------
@pytest.mark.parametrize("market", sorted(MARKET_CODES))
def test_every_output_carries_as_of(market):
    m = compute(p6_case(), symbol="T", market=market)
    assert m["as_of"]
    assert evaluate(m)["as_of"] == m["as_of"]
    assert score(m)["as_of"] == m["as_of"]
    assert all(x["as_of"] == m["as_of"] for x in rank([m]))
