"""core/backtest.py 測試。

最重要的一件事只有一個：snapshot() 算 as_of 分數時，絕對不能碰到
asof 之後的任何一筆資料 —— 這是整個復盤功能唯一有意義的前提。
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import core.backtest as bt
from data.base import MarketAdapter


def _series(closes, *, start="2025-01-01"):
    n = len(closes)
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"open": c, "high": c * 1.005, "low": c * 0.995, "close": c,
         "volume": np.full(n, 10_000.0)},
        index=pd.bdate_range(start, periods=n),
    )


class TimelineAdapter(MarketAdapter):
    """每檔股票有一條連續的完整時間軸；_fetch 依 start/end 裁切，並記錄
    每一次被要求的 end 日期，好讓測試證明呼叫端從未要求超過 asof 的資料。
    """

    market = "TW"

    def __init__(self, timelines: dict[str, pd.DataFrame]):
        self.timelines = timelines
        self.requested_ends: list[date] = []

    def _fetch(self, symbol, start, end):
        self.requested_ends.append(end)
        full = self.timelines.get(symbol)
        if full is None:
            return pd.DataFrame()
        return full.loc[str(start):str(end)]

    def limit_pct(self, symbol):
        return None


def _flat_then_rise(n_before=150, n_after=60, seed=1):
    """asof 之前走平，之後上漲 —— 用來檢查 forward_return 算得對不對。"""
    rng = np.random.default_rng(seed)
    before = 100 + rng.normal(0, 0.3, n_before)
    after = 100 * np.exp(np.linspace(0, 0.15, n_after))
    return _series(np.concatenate([before, after]))


def _uptrend_then_continue(n_before=150, n_after=60, seed=2):
    rng = np.random.default_rng(seed)
    before = 100 * np.exp(np.linspace(0, 0.3, n_before)) + rng.normal(0, 0.2, n_before)
    after = before[-1] * np.exp(np.linspace(0, 0.10, n_after))
    return _series(np.concatenate([before, after]))


def _downtrend_then_continue(n_before=150, n_after=60, seed=3):
    rng = np.random.default_rng(seed)
    before = 100 * np.exp(np.linspace(0, -0.3, n_before)) + rng.normal(0, 0.2, n_before)
    after = before[-1] * np.exp(np.linspace(0, -0.10, n_after))
    return _series(np.concatenate([before, after]))


ASOF = (pd.bdate_range("2025-01-01", periods=150)[-1]).date()      # 最後一根「之前」的日期
UNTIL = (pd.bdate_range("2025-01-01", periods=150 + 60)[-1]).date()  # 最後一根「之後」的日期


@pytest.fixture
def timeline_adapter(monkeypatch):
    timelines = {
        "UP": _uptrend_then_continue(),
        "DOWN": _downtrend_then_continue(),
        "FLAT_RISE": _flat_then_rise(),
    }
    ad = TimelineAdapter(timelines)
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    return ad


# ---------- 最重要的性質：不偷看未來 ----------
def test_snapshot_never_requests_data_past_asof(timeline_adapter):
    bt.snapshot("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF)
    assert all(e <= ASOF for e in timeline_adapter.requested_ends), (
        f"snapshot() 要求了超過 asof（{ASOF}）的資料：{timeline_adapter.requested_ends}"
    )


def test_snapshot_uses_use_cache_false(monkeypatch):
    """復盤不能用共用快取（見模組開頭說明），否則會污染平常推薦排序用的快取。"""
    calls = []

    class Recording(MarketAdapter):
        market = "TW"

        def _fetch(self, symbol, start, end):
            return pd.DataFrame()   # 不會被用到，ohlcv() 已被下面覆寫

        def ohlcv(self, symbol, start, end=None, **kwargs):
            calls.append(kwargs.get("use_cache"))
            return _series([100.0] * 65)

    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: Recording())
    bt.snapshot("TW", ["X"], asof=ASOF)
    bt.forward_return("TW", "X", asof=ASOF, until=UNTIL)
    assert calls == [False, False]


# ---------- snapshot() ----------
def test_snapshot_ranks_by_score_descending(timeline_adapter):
    out = bt.snapshot("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF)
    scores = [r["score"] for r in out["ranked"]]
    assert scores == sorted(scores, reverse=True)
    assert [r["symbol"] for r in out["ranked"]][0] == "UP"   # 多頭排列分數最高


def test_snapshot_reports_missing_symbol_as_failed(timeline_adapter):
    out = bt.snapshot("TW", ["UP", "NOT_LISTED"], asof=ASOF)
    assert [f["symbol"] for f in out["failed"]] == ["NOT_LISTED"]
    assert [r["symbol"] for r in out["ranked"]] == ["UP"]


def test_snapshot_rejects_symbol_delisted_before_asof(monkeypatch):
    """asof 前一週內完全沒交易資料 → 視為當時不存在，不能假裝算得出推薦。

    資料量刻意夠多（150 筆，遠超過 60 筆門檻），只是最後一筆停在 asof
    前 30 天 —— 用來跟「單純資料不足」的失敗原因區分開。
    """
    cutoff = ASOF - timedelta(days=30)
    idx = pd.bdate_range(end=cutoff, periods=150)
    delisted = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
         "volume": 10_000.0}, index=idx,
    )
    ad = TimelineAdapter({"DEAD": delisted})
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    out = bt.snapshot("TW", ["DEAD"], asof=ASOF)
    assert out["ranked"] == []
    assert "無資料" in out["failed"][0]["error"]


# ---------- forward_return() ----------
def test_forward_return_arithmetic(timeline_adapter):
    fr = bt.forward_return("US", "FLAT_RISE", asof=ASOF, until=UNTIL)
    df = timeline_adapter.timelines["FLAT_RISE"]
    entry = df.loc[str(ASOF):str(UNTIL)]["close"].iloc[0]
    exit_ = df.loc[str(ASOF):str(UNTIL)]["close"].iloc[-1]
    expected_gross = (exit_ / entry - 1) * 100
    assert fr["gross_return_pct"] == pytest.approx(expected_gross, abs=0.01)
    assert fr["cost_pct"] == 0.0                              # 美股成本 0bps
    assert fr["net_return_pct"] == fr["gross_return_pct"]


def test_forward_return_deducts_market_cost(timeline_adapter):
    fr_tw = bt.forward_return("TW", "UP", asof=ASOF, until=UNTIL)
    fr_us = bt.forward_return("US", "UP", asof=ASOF, until=UNTIL)
    assert fr_tw["gross_return_pct"] == fr_us["gross_return_pct"]   # 同一條時間軸
    assert fr_tw["cost_pct"] == 0.585                                # 58.5bps
    assert fr_tw["net_return_pct"] == pytest.approx(fr_tw["gross_return_pct"] - 0.585, abs=0.01)


def test_forward_return_none_when_insufficient_data(monkeypatch):
    ad = TimelineAdapter({"THIN": _series([100.0], start=str(ASOF))})
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    assert bt.forward_return("TW", "THIN", asof=ASOF, until=UNTIL) is None


def test_forward_return_rejects_until_before_asof(timeline_adapter):
    with pytest.raises(ValueError, match="asof"):
        bt.forward_return("TW", "UP", asof=UNTIL, until=ASOF)


# ---------- review()：組裝與摘要統計 ----------
def test_review_assembles_rows_and_summary(timeline_adapter):
    out = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL)
    assert out["asof"] == str(ASOF)
    assert out["until"] == str(UNTIL)
    assert len(out["rows"]) == 3
    assert all(r["forward"] is not None for r in out["rows"])
    assert out["summary"]["n"] == 3


def test_review_detects_positive_score_return_relationship(timeline_adapter):
    """UP 分數最高且漲最多，DOWN 分數最低且跌最多 —— 這組資料是刻意這樣
    設計的，摘要統計要能反映出「高分表現確實比較好」。"""
    out = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL)
    s = out["summary"]
    assert s["avg_return_top_half_pct"] > s["avg_return_bottom_half_pct"]
    assert s["score_return_correlation"] > 0


def test_review_top_limits_to_highest_scores(timeline_adapter):
    out = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL, top=1)
    assert [r["symbol"] for r in out["rows"]] == ["UP"]


def test_review_calls_on_progress_once_per_row(timeline_adapter):
    calls = []
    bt.review("TW", ["UP", "DOWN"], asof=ASOF, until=UNTIL,
              on_progress=lambda done, total: calls.append((done, total)))
    assert calls == [(1, 2), (2, 2)]


def test_review_excludes_missing_forward_from_summary_but_keeps_in_rows(monkeypatch):
    """THIN 在 asof 當時資料充足（snapshot 算得出分數），但資料就停在 asof
    當天，之後完全沒有 —— forward_return 該回傳 None，且不能讓整檔從
    rows 裡消失（使用者要看得到「這檔算得出推薦，但沒有後續資料可比對」）。
    """
    good = _uptrend_then_continue()
    thin = _series([100.0] * 150, start="2025-01-01")   # 剛好停在 asof 當天
    ad = TimelineAdapter({"GOOD": good, "THIN": thin})
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)

    out = bt.review("TW", ["GOOD", "THIN"], asof=ASOF, until=UNTIL)
    assert len(out["rows"]) == 2                    # 兩檔都在 rows 裡
    assert out["summary"]["n"] == 1                  # 摘要只算得出資料完整的那檔
    thin_row = next(r for r in out["rows"] if r["symbol"] == "THIN")
    assert thin_row["forward"] is None


# ---------- _correlation() 邊界情況 ----------
def test_correlation_none_for_too_few_points():
    assert bt._correlation([1.0], [2.0]) is None
    assert bt._correlation([], []) is None


def test_correlation_none_when_no_variance():
    assert bt._correlation([5.0, 5.0, 5.0], [1.0, 2.0, 3.0]) is None


def test_correlation_perfect_positive():
    assert bt._correlation([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == pytest.approx(1.0)


def test_correlation_perfect_negative():
    assert bt._correlation([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)


# ---------- 對照基準與逐因子 IC（校準用） ----------
def test_summary_includes_benchmark_and_excess_return(timeline_adapter):
    out = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL)
    s = out["summary"]
    assert s["benchmark_n"] == 3
    assert s["benchmark_avg_return_pct"] is not None
    # 沒有 top 時「選出來的」就是全部，超額報酬必定為 0
    assert s["excess_return_pct"] == 0.0


def test_benchmark_covers_whole_universe_even_when_top_limits_rows(timeline_adapter):
    """top 只影響顯示與選股組合，對照基準一定要涵蓋整池 —— 否則拿選出來
    的那幾檔當自己的基準，超額報酬永遠是 0，整個比較失去意義。"""
    out = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL, top=1)
    s = out["summary"]
    assert len(out["rows"]) == 1          # 只顯示前 1 檔
    assert s["n"] == 1
    assert s["benchmark_n"] == 3          # 但基準看的是三檔
    assert s["excess_return_pct"] != 0.0


def test_excess_return_is_negative_when_picks_lag_the_universe(monkeypatch):
    """選股輸給大盤時要如實報負的超額報酬，不能只報「平均賺 X%」就交差。"""
    rng = np.random.default_rng(7)
    # LOSER：as_of 前是完美多頭（分數最高），之後反轉下跌
    before_up = 100 * np.exp(np.linspace(0, 0.30, 150)) + rng.normal(0, 0.15, 150)
    loser = _series(np.concatenate([before_up, before_up[-1] * np.exp(np.linspace(0, -0.20, 60))]))
    # WINNER：as_of 前弱勢（分數低），之後大漲
    before_dn = 100 * np.exp(np.linspace(0, -0.10, 150)) + rng.normal(0, 0.15, 150)
    winner = _series(np.concatenate([before_dn, before_dn[-1] * np.exp(np.linspace(0, 0.40, 60))]))

    ad = TimelineAdapter({"LOSER": loser, "WINNER": winner})
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)

    out = bt.review("TW", ["LOSER", "WINNER"], asof=ASOF, until=UNTIL, top=1)
    s = out["summary"]
    assert out["rows"][0]["symbol"] == "LOSER"      # 評分挑了後來下跌的那檔
    assert s["excess_return_pct"] < 0                # 必須誠實反映為負貢獻


def test_factor_stats_reports_every_factor(timeline_adapter):
    out = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL)
    stats = out["summary"]["factor_stats"]
    assert set(stats) == {"trend", "momentum", "volume", "position", "volatility"}
    for v in stats.values():
        assert set(v) == {"ic", "std", "std_ratio", "concentration_pct", "reliable"}


def test_factor_ic_detects_which_factor_carries_the_signal(monkeypatch):
    """逐因子 IC 是校準權重的唯一依據，必須真的分得出哪個因子有預測力。

    這裡用四檔標的，讓 trend 分數與後續報酬同向、完全一致，
    trend 的 IC 應該明顯為正。"""
    timelines = {}
    # 四種 as_of 前的趨勢強度，之後的漲幅依同樣順序排列
    for name, slope_before, slope_after in [
        ("A", 0.30, 0.20), ("B", 0.15, 0.10),
        ("C", -0.05, -0.05), ("D", -0.25, -0.15),
    ]:
        before = 100 * np.exp(np.linspace(0, slope_before, 150))
        after = before[-1] * np.exp(np.linspace(0, slope_after, 60))
        timelines[name] = _series(np.concatenate([before, after]))

    ad = TimelineAdapter(timelines)
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)

    stats = bt.review("TW", list(timelines), asof=ASOF, until=UNTIL)["summary"]["factor_stats"]
    assert stats["trend"]["ic"] is not None and stats["trend"]["ic"] > 0.5


def test_factor_stats_empty_when_no_priced_rows(monkeypatch):
    ad = TimelineAdapter({})
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    out = bt.review("TW", ["NOPE"], asof=ASOF, until=UNTIL)
    assert out["summary"] == {"n": 0}


def test_quota_exceeded_aborts_whole_batch(monkeypatch):
    """額度用完時整批中止，不要對剩下 39 檔重複撞同一面牆。"""
    from data.base import QuotaExceeded

    attempted = []

    class QuotaAdapter(MarketAdapter):
        market = "TW"

        def _fetch(self, symbol, start, end):
            return pd.DataFrame()

        def ohlcv(self, symbol, start, end=None, **kwargs):
            attempted.append(symbol)
            raise QuotaExceeded("額度用完")

    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: QuotaAdapter())
    with pytest.raises(QuotaExceeded):
        bt.snapshot("TW", ["A", "B", "C", "D"], asof=ASOF)
    assert attempted == ["A"], f"應該第一檔就中止，實際試了 {attempted}"


# ---------- 分位數分組 ----------
def _many_symbol_adapter(n_symbols=15, *, monotonic=True):
    """建立 n 檔標的：monotonic=True 時 as_of 分數越高、之後報酬越高。"""
    timelines = {}
    for i in range(n_symbols):
        slope_before = 0.30 - i * 0.04          # 由強到弱
        slope_after = slope_before if monotonic else (0.30 - ((i * 7) % n_symbols) * 0.04)
        before = 100 * np.exp(np.linspace(0, slope_before, 150))
        after = before[-1] * np.exp(np.linspace(0, slope_after, 60))
        timelines[f"S{i:02d}"] = _series(np.concatenate([before, after]))
    return TimelineAdapter(timelines)


def test_score_buckets_split_whole_universe(monkeypatch):
    ad = _many_symbol_adapter(15)
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    out = bt.review("TW", list(ad.timelines), asof=ASOF, until=UNTIL)
    buckets = out["summary"]["score_buckets"]

    assert [b["label"] for b in buckets] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    assert sum(b["n"] for b in buckets) == 15       # 每一檔都要被分到組
    assert all(b["excess_pct"] is not None for b in buckets)


def test_score_buckets_are_monotonic_when_ranking_works(monkeypatch):
    """排序有效時，分組報酬應該由高分到低分遞減 —— 這是比單一相關係數
    更耐看的判準。"""
    ad = _many_symbol_adapter(15, monotonic=True)
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    buckets = bt.review("TW", list(ad.timelines), asof=ASOF,
                        until=UNTIL)["summary"]["score_buckets"]
    returns = [b["avg_return_pct"] for b in buckets]
    assert returns == sorted(returns, reverse=True), f"分組報酬不單調：{returns}"
    assert buckets[0]["excess_pct"] > 0 > buckets[-1]["excess_pct"]


def test_score_buckets_empty_when_too_few_symbols(timeline_adapter):
    """只有三檔時切不出有意義的分組，寧可不給，也不要拿單一檔當一組。"""
    out = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL)
    assert out["summary"]["score_buckets"] == []


def test_selected_is_whole_universe_flag(timeline_adapter):
    """top 留空時超額報酬必然是 0，要有旗標讓顯示層不要誤導成「評分沒用」。"""
    whole = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL)
    assert whole["summary"]["selected_is_whole_universe"] is True

    subset = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF, until=UNTIL, top=1)
    assert subset["summary"]["selected_is_whole_universe"] is False


# ---------- 因子離散度診斷：分辨真 IC 與假 IC ----------
def _rows_with_factor(points: list[float], returns: list[float], weight=10):
    """直接組出 _factor_stats 要的最小結構，不經過真實評分邏輯 ——
    這樣測的是診斷機制本身，不依賴任何一個因子剛好壞掉。"""
    return [
        {"forward": {"net_return_pct": r},
         "breakdown": [{"factor": "fake", "weight": weight, "points": p}]}
        for p, r in zip(points, returns)
    ]


def test_low_variance_factor_is_flagged_unreliable():
    """診斷機制本身：一個對多數標的給出相同分數的因子，就算 IC 很大也
    必須標記為不可信，因為那是被少數離群值帶出來的。"""
    from config import rules as rules_cfg

    # 10 檔同分、2 檔離群 —— 離群那兩檔的報酬刻意極端，把 IC 拉到很大
    points = [10.0] * 10 + [2.0, 2.0]
    returns = [1.0, -1.0, 0.5, -0.5, 1.0, -1.0, 0.5, -0.5, 1.0, -1.0, 40.0, 38.0]
    stats = bt._factor_stats(_rows_with_factor(points, returns), rules_cfg())

    fake = stats["fake"]
    assert abs(fake["ic"]) > 0.8, fake            # IC 看起來很漂亮
    assert fake["concentration_pct"] >= 60.0, fake
    assert fake["reliable"] is False, f"高度集中的因子應標記為不可信：{fake}"


def test_volatility_factor_no_longer_concentrates(monkeypatch):
    """回歸測試：這組資料（10 檔低波動 + 2 檔高波動）在改成連續評分之前，
    volatility 的集中度是 75%、被判定為不可信 —— 因為舊版對 0.15~0.45
    一律給滿分。改成對數鐘形曲線後，同一組資料的集中度應該大幅下降。"""
    timelines = {}
    for i in range(12):
        vol = 0.012 if i < 10 else 0.055        # 前 10 檔低波動、後 2 檔高波動
        rng = np.random.default_rng(100 + i)
        before = 100 * np.exp(np.cumsum(rng.normal(0.001, vol, 150)))
        after = before[-1] * np.exp(np.cumsum(rng.normal(0.002, vol, 60)))
        timelines[f"V{i:02d}"] = _series(np.concatenate([before, after]))

    ad = TimelineAdapter(timelines)
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    stats = bt.review("TW", list(timelines), asof=ASOF,
                      until=UNTIL)["summary"]["factor_stats"]

    vol_stat = stats["volatility"]
    assert vol_stat["concentration_pct"] < 40.0, \
        f"連續評分後不該再高度集中：{vol_stat}"
    assert vol_stat["reliable"] is True, vol_stat


def test_well_spread_factor_is_flagged_reliable(monkeypatch):
    """反面：分數分散的因子，IC 才可信。"""
    ad = _many_symbol_adapter(15, monotonic=True)
    monkeypatch.setattr("core.backtest.get_adapter", lambda market, **kw: ad)
    stats = bt.review("TW", list(ad.timelines), asof=ASOF,
                      until=UNTIL)["summary"]["factor_stats"]
    assert stats["trend"]["reliable"] is True, stats["trend"]


def test_std_ratio_is_comparable_across_weights(timeline_adapter):
    """std_ratio = std ÷ 權重，所以權重 30 的 trend 和權重 10 的
    volatility 才能放在一起比較。"""
    stats = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF,
                      until=UNTIL)["summary"]["factor_stats"]
    for name, v in stats.items():
        assert 0.0 <= v["std_ratio"] <= 1.0, (name, v)
        assert 0.0 < v["concentration_pct"] <= 100.0, (name, v)


def test_ic_diagnostics_thresholds_come_from_config(timeline_adapter):
    """鐵則 2：門檻要能從 config 調，不是寫死在程式裡。"""
    from config import rules as rules_cfg

    loose = rules_cfg()
    loose["score"]["ic_diagnostics"] = {"min_std_ratio": 0.0,
                                        "max_concentration_pct": 100.0}
    stats = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF,
                      until=UNTIL, cfg=loose)["summary"]["factor_stats"]
    assert all(v["reliable"] for v in stats.values())

    strict = rules_cfg()
    strict["score"]["ic_diagnostics"] = {"min_std_ratio": 0.99,
                                         "max_concentration_pct": 0.0}
    stats = bt.review("TW", ["UP", "DOWN", "FLAT_RISE"], asof=ASOF,
                      until=UNTIL, cfg=strict)["summary"]["factor_stats"]
    assert not any(v["reliable"] for v in stats.values())


def test_std_and_concentration_helpers():
    assert bt._std([5.0, 5.0, 5.0]) == 0.0
    assert bt._std([1.0]) == 0.0
    assert bt._std([0.0, 10.0]) == 5.0
    assert bt._concentration_pct([1.0, 1.0, 1.0, 2.0]) == 75.0
    assert bt._concentration_pct([1.0, 2.0, 3.0, 4.0]) == 25.0
    assert bt._concentration_pct([]) == 0.0
