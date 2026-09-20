"""復盤結果的純文字摘要。

目的是讓使用者一鍵複製貼給別人／貼進對話，取代截圖 —— 所以重點是
「統計區塊要完整」而不是「好看」，並且不能塞進 40 列個股明細。
"""

from __future__ import annotations

import pytest

from web.summary import review_text


def _result(**over):
    out = {
        "market": "US", "asof": "2026-02-01", "until": "2026-08-31",
        "failed": [],
        "rows": [
            {"symbol": f"S{i:02d}", "score": 90 - i * 5,
             "forward": {"net_return_pct": 20 - i * 3}}
            for i in range(12)
        ],
        "summary": {
            "n": 12, "avg_return_pct": 10.46, "win_rate_pct": 67.5,
            "avg_return_top_half_pct": 12.53, "avg_return_bottom_half_pct": 8.38,
            "score_return_correlation": 0.078,
            "benchmark_n": 12, "benchmark_avg_return_pct": 10.46,
            "excess_return_pct": 0.0, "selected_is_whole_universe": True,
            "score_buckets": [
                {"label": "Q1", "n": 3, "score_range": [80, 90],
                 "avg_return_pct": 17.0, "excess_pct": 6.54},
                {"label": "Q2", "n": 3, "score_range": [65, 75],
                 "avg_return_pct": 8.0, "excess_pct": -2.46},
            ],
            "factor_stats": {
                "trend": {"ic": 0.131, "std": 11.06, "std_ratio": 0.369,
                          "concentration_pct": 41.7, "reliable": True},
                "volatility": {"ic": -0.517, "std": 2.74, "std_ratio": 0.274,
                               "concentration_pct": 75.0, "reliable": False},
            },
        },
    }
    out.update(over)
    return out


def test_includes_every_statistics_block():
    text = review_text(_result())
    for expected in ["US 復盤", "2026-02-01", "2026-08-31",
                     "樣本 12", "10.46%", "67.5%", "0.078",
                     "對照基準", "分數前半", "分數分組", "Q1", "Q2",
                     "逐因子", "trend", "volatility"]:
        assert expected in text, f"摘要缺少 {expected}\n{text}"


def test_marks_unreliable_factor():
    text = review_text(_result())
    assert "鑑別度不足" in text
    assert "可信" in text


def test_explains_zero_excess_when_whole_universe_selected():
    """超額 0.00% 是結構使然，不是評分無效 —— 純文字版也要講清楚，
    否則貼給別人看只會被誤讀。"""
    assert "結構使然" in review_text(_result())


def test_omits_the_long_detail_table():
    """40 列明細正是螢幕截圖會被截斷的原因，摘要只留最高／最低各 5 檔。"""
    text = review_text(_result())
    listed = [f"S{i:02d}" for i in range(12) if f"S{i:02d}" in text]
    assert len(listed) == 10, listed          # 最高 5 ＋ 最低 5
    assert "S05" not in text and "S06" not in text     # 中間的不列


def test_short_universe_does_not_duplicate_examples():
    """標的少於 10 檔時不該把同一批印兩次。"""
    out = _result(rows=[{"symbol": f"S{i}", "score": 90 - i,
                         "forward": {"net_return_pct": 1.0}} for i in range(6)])
    text = review_text(out)
    assert text.count("S0") == 1 and "分數最低" not in text


def test_handles_empty_result():
    out = _result(summary={"n": 0},
                  failed=[{"symbol": "AAPL", "error": "沒資料"}])
    text = review_text(out)
    assert "沒有任何標的" in text and "AAPL" in text


def test_none_values_render_as_dash_not_python_none():
    out = _result()
    out["summary"]["score_return_correlation"] = None
    out["summary"]["factor_stats"]["trend"]["ic"] = None
    text = review_text(out)
    assert "None" not in text
    assert "—" in text


def test_failed_symbols_are_reported():
    out = _result(failed=[{"symbol": "BAD", "error": "資料不足"}])
    assert "取得失敗 1 檔" in review_text(out) and "BAD" in review_text(out)


def test_table_headers_are_ascii_for_stable_alignment():
    """欄位標題不能用中文：全形字在不同聊天視窗的寬度算法不一樣，
    貼出去一定歪。中文只允許出現在最後一欄（歪了也不影響閱讀）。"""
    text = review_text(_result())
    header = next(l for l in text.splitlines() if "factor" in l)
    body = next(l for l in text.splitlines() if "trend" in l)

    ascii_header = header.split("判定")[0]
    assert ascii_header.isascii(), f"標題含全形字會對不齊：{header!r}"
    # 標題與資料列的欄位右緣要對齊
    assert ascii_header.index("conc%") + len("conc%") == body.index("%") + 1


def test_ic_shows_explicit_sign():
    """貼出來時正負要一眼可辨，不能只有負數有符號。"""
    text = review_text(_result())
    assert "+0.131" in text and "-0.517" in text
