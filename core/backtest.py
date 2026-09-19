"""進場推薦的事後檢視（復盤）—— 拿一個過去的 as_of 日期算出當時的推薦
排序，再看到 until（預設今天）的實際報酬，藉此檢驗評分排序有沒有預測力：
分數高的標的，後來真的表現比較好嗎？

跟 CLAUDE.md 待辦清單裡「backtest.py —— 逐條規則獨立回測，砍掉期望值
為負的」不是同一件事：那個是要驗證 core/rules.py 裡 P6/P7/... 每一條
出場規則本身的期望值，需要逐日模擬規則觸發與停損執行；這裡只驗證
core/recommend.py 的進場評分（score）排序品質，是「買進持有到某天」
的單點報酬比對，不模擬持有期間規則觸發的出場。逐條規則回測仍是待辦。

一樣不分市場：市場差異只透過 config/markets.yaml 的 cost_bps 進來，
core/ 底下沒有任何 if market 分支（鐵則 1）。

刻意不用快取（use_cache=False）：data/cache.py 的有效性判斷只看
requested_start，不看 requested end——如果復盤把「只到 asof 為止」的
資料存進共用快取，之後正常的「到今天為止」查詢會被誤判成快取夠用，
拿到在 asof 被截斷的舊資料。復盤本來就不是天天跑好幾次的操作，
用犧牲一點速度換正確性划算。
"""

from __future__ import annotations

from datetime import date, timedelta

from config import market_cfg
from core.indicators import compute
from core.recommend import score
from data.base import get_adapter

# 抓多少天的歷史來算 as_of 當天的指標。這不是規則門檻（鐵則 2 管的是判斷
# 用的數值），只是「要抓多少資料」的視窗大小，跟 cli.py／web/logic.py
# 的 420 天是同一個考量：210 個交易日，覆蓋 60MA 和 60 日高點回撤還有餘裕。
DEFAULT_LOOKBACK_DAYS = 420


def _to_date(x) -> date:
    if isinstance(x, date):
        return x
    return date.fromisoformat(str(x))


def snapshot(market: str, symbols: list[str], *, asof,
             lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict:
    """算出 as_of 當天「只用當時看得到的資料」算出的推薦評分。

    只拿 [asof - lookback_days, asof] 這段資料，絕對不碰 asof 之後的
    任何一筆 —— 這是整個復盤功能唯一有意義的前提，不能妥協。
    """
    asof = _to_date(asof)
    start = asof - timedelta(days=lookback_days)
    ad = get_adapter(market)

    ranked, failed = [], []
    for sym in symbols:
        try:
            df = ad.ohlcv(sym, start, asof, use_cache=False)
            if len(df) < 60:
                raise ValueError(f"as_of 前只取到 {len(df)} 筆，不足 60 筆")
            last_bar = df.index.max().date()
            if (asof - last_bar).days > 7:
                # as_of 前一週內完全沒有交易資料：這檔在當時很可能還沒
                # 上市、已下市，或代碼在那個時間點根本不存在，不該假裝
                # 算得出推薦。
                raise ValueError(f"as_of 附近無資料，最後一筆是 {last_bar}")
            m = compute(df, symbol=sym, market=market)
            s = score(m)
            ranked.append(s)
        except Exception as e:
            failed.append({"symbol": sym, "error": str(e)})

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return {"ranked": ranked, "failed": failed}


def forward_return(market: str, symbol: str, *, asof, until=None) -> dict | None:
    """asof 收盤到 until 收盤的報酬率，已扣掉來回交易成本（鐵則 2：
    成本一律讀 config，不寫死）。

    資料不足兩筆（例如標的後來下市、until 早於實際有資料的日期）回傳
    None，呼叫端要處理這個情況，不能假裝算得出報酬。
    """
    asof = _to_date(asof)
    until = _to_date(until) if until else date.today()
    if until <= asof:
        raise ValueError(f"until（{until}）必須晚於 asof（{asof}）")

    ad = get_adapter(market)
    df = ad.ohlcv(symbol, asof, until, use_cache=False)
    if len(df) < 2:
        return None

    entry, exit_ = float(df["close"].iloc[0]), float(df["close"].iloc[-1])
    gross_pct = (exit_ / entry - 1) * 100
    cost_pct = market_cfg(market)["cost_bps"] / 100          # bps → %

    return {
        "entry_date": str(df.index[0].date()),
        "entry_price": round(entry, 2),
        "exit_date": str(df.index[-1].date()),
        "exit_price": round(exit_, 2),
        "gross_return_pct": round(gross_pct, 2),
        "cost_pct": cost_pct,
        "net_return_pct": round(gross_pct - cost_pct, 2),
    }


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    """Pearson 相關係數。手算是為了不為這一個統計量多引入一個相依套件；
    n < 2 或任一邊變異數為 0（例如樣本全部同分）回傳 None 而不是報錯。"""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx == 0 or vy == 0:
        return None
    return round(cov / (vx ** 0.5 * vy ** 0.5), 3)


def _summarize(rows: list[dict]) -> dict:
    """rows 已經是「有 forward 報酬」的子集，且維持 snapshot() 依分數
    由高到低排序。"""
    if not rows:
        return {"n": 0}

    scores_ = [r["score"] for r in rows]
    returns = [r["forward"]["net_return_pct"] for r in rows]
    n = len(rows)

    half = n // 2
    top_half = returns[:half] if half else returns
    bottom_half = returns[-half:] if half else returns

    return {
        "n": n,
        "avg_return_pct": round(sum(returns) / n, 2),
        "win_rate_pct": round(sum(1 for x in returns if x > 0) / n * 100, 1),
        "avg_return_top_half_pct": round(sum(top_half) / len(top_half), 2),
        "avg_return_bottom_half_pct": round(sum(bottom_half) / len(bottom_half), 2),
        "score_return_correlation": _correlation(scores_, returns),
    }


def review(market: str, symbols: list[str], *, asof, until=None,
           top: int | None = None, on_progress=None) -> dict:
    """完整復盤：as_of 的推薦排序 ＋ 之後到 until 的實際報酬 ＋ 摘要統計。

    on_progress(done, total) 在每算完一檔的 forward_return 後呼叫一次，
    給網站版的背景工作回報進度用。
    """
    asof_d, until_d = _to_date(asof), _to_date(until) if until else date.today()
    snap = snapshot(market, symbols, asof=asof_d)
    rows = snap["ranked"][:top] if top else snap["ranked"]

    total = len(rows)
    for i, row in enumerate(rows, 1):
        row["forward"] = forward_return(market, row["symbol"], asof=asof_d, until=until_d)
        if on_progress:
            on_progress(i, total)

    priced = [r for r in rows if r["forward"] is not None]
    return {
        "market": market,
        "asof": str(asof_d),
        "until": str(until_d),
        "rows": rows,
        "failed": snap["failed"],
        "summary": _summarize(priced),
    }
