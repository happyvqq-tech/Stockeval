"""網站邏輯的共用部分 —— 只是組裝呼叫，不做任何計算。

獨立成一個模組（而不是散在 app.py 的路由函式裡），是為了讓測試可以
monkeypatch 這裡的 get_adapter，不用真的連網路。
"""

from __future__ import annotations

from datetime import date, timedelta

from core.indicators import compute, is_limit_down
from core.recommend import rank, score
from core.rules import evaluate
from data.base import get_adapter


def default_start() -> str:
    return str(date.today() - timedelta(days=420))      # 約 280 個交易日


def fetch_metrics(market: str, symbol: str, start: str | None = None):
    """抓資料並算指標。回傳 (m, df, adapter)。資料不足直接丟例外。"""
    ad = get_adapter(market)
    df = ad.ohlcv(symbol, start or default_start())
    if len(df) < 60:
        raise ValueError(f"只取到 {len(df)} 筆，不足 60 筆")
    m = compute(df, symbol=symbol, market=market)
    return m, df, ad


def run_check(market: str, symbol: str, unrealized: float) -> dict:
    """個股檢查頁的完整流程：抓資料 → 出場判定 → 進場評分。"""
    m, df, ad = fetch_metrics(market, symbol)

    limit_down = False
    lp = ad.limit_pct(symbol)
    if lp:
        limit_down = is_limit_down(df, lp)

    out = evaluate(m, unrealized_pct=unrealized, limit_down=limit_down, adjusted=ad.adjusted)
    out["recommendation"] = score(m)
    out["adjusted"] = ad.adjusted
    return out


def run_scan(market: str, symbols: list[str], *, top=None, min_score: float = 0.0,
             on_progress=None) -> dict:
    """推薦排序頁的完整流程：整批抓資料 → 評分 → 排序。

    單一標的失敗不中斷整批（照抄 cli.py 的作法），同一個 adapter 實例
    循序處理全部標的 —— 不要平行化，FinMind 免費層有速率限制。
    """
    ad = get_adapter(market)
    metrics, failed = [], []
    any_unadjusted = False

    for i, sym in enumerate(symbols, 1):
        try:
            df = ad.ohlcv(sym, default_start())
            if len(df) < 60:
                raise ValueError(f"只取到 {len(df)} 筆，不足 60 筆")
            if not ad.adjusted:
                any_unadjusted = True          # 記錄整批中「有沒有任何一檔」未還原權息
            metrics.append(compute(df, symbol=sym, market=market))
        except Exception as e:
            failed.append({"symbol": sym, "error": str(e)})
        if on_progress:
            on_progress(i, len(symbols))

    ranked = rank(metrics, top=top, min_score=min_score)
    return {"ranked": ranked, "failed": failed, "any_unadjusted": any_unadjusted}


def run_review(market: str, symbols: list[str], *, asof: str, until: str | None = None,
               top: int | None = None, on_progress=None) -> dict:
    """復盤頁的完整流程：某天的推薦排序 vs 之後到 until 的實際報酬。

    核心邏輯全部在 core/backtest.py，這裡只是原封不動轉呼叫。注意
    get_adapter 是在 core/backtest.py 裡呼叫的，不是這個檔案，所以
    測試要 monkeypatch core.backtest.get_adapter，跟 run_scan /
    run_check 監控 web.logic.get_adapter 不是同一個地方。
    """
    from core.backtest import review
    return review(market, symbols, asof=asof, until=until, top=top, on_progress=on_progress)
