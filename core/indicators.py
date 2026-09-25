"""指標計算 —— 只有一份，不分市場。

輸入必須是 data/base.py normalize() 後的統一格式（還原權息、已剔除停牌）。
輸出是一個扁平 dict，每個欄位都帶 as_of 日期。

這個 dict 就是送給 LLM 的全部數字。LLM 不做任何計算。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import market_cfg, rules as rules_cfg

# 均線/回看窗格（5/10/20/60）不是「門檻」而是指標的定義本身，
# 改掉 ma20 就不叫 ma20 了，所以留在程式裡。
# 真正可調的門檻（最少筆數、黑K跌幅）一律從 config 讀 —— 鐵則 2。


def _r(x, nd=2):
    """安全四捨五入：NaN 一律轉成 None，送進 JSON 才不會壞掉。"""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) or np.isinf(f) else round(f, nd)


def compute(df: pd.DataFrame, *, symbol: str = "", market: str = "") -> dict:
    """計算單一標的的全部技術指標快照。"""
    try:
        min_bars = market_cfg(market)["min_bars"]
    except KeyError:
        min_bars = 60                      # 市場未指定時的保底值
    if len(df) < min_bars:
        raise ValueError(f"[{market}:{symbol}] 資料不足 {min_bars} 筆，拒絕計算")

    # 黑K跌破的跌幅門檻與 P6 共用同一個設定值，避免兩處各寫一份而失聯
    black_k_drop = rules_cfg()["p6"]["drop_pct"]

    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    vol_ma5 = v.rolling(5).mean()

    i = -1
    prev_close = c.iloc[-2]
    today = c.iloc[i]

    # --- 連續跌破 20MA 天數（由最後一日往回數，遇到站回就停）---
    below = (c < ma20)
    days_below = 0
    for flag in reversed(below.tolist()):
        if flag:
            days_below += 1
        else:
            break

    # --- 近 60 日高點回撤 (V6-2) ---
    # 用盤中最高價而不是收盤價：真實的回撤是從最高點算起，用收盤價會低估。
    high_60 = h.iloc[-60:].max()

    # --- 年化波動率（判定是否為高波動成長股，觸發 V6-2a）---
    ret = c.pct_change().dropna()
    ann_vol = ret.iloc[-60:].std() * np.sqrt(252) if len(ret) >= 60 else np.nan

    # --- 跳空 (P11) ---
    gap_pct = (df["open"].iloc[i] / prev_close - 1) * 100

    return {
        # 身分與時效 —— 每筆輸出都必須帶，這是「資料時效鐵則」的唯一可靠實作
        "symbol": symbol,
        "market": market,
        "as_of": str(df.index[i].date()),
        "bars": len(df),

        # 價
        "close": _r(today),
        "prev_close": _r(prev_close),
        "chg_pct": _r((today / prev_close - 1) * 100),
        "open": _r(df["open"].iloc[i]),
        "high": _r(h.iloc[i]),
        "low": _r(l.iloc[i]),

        # 均線
        "ma5": _r(ma5.iloc[i]),
        "ma10": _r(ma10.iloc[i]),
        "ma20": _r(ma20.iloc[i]),
        "ma60": _r(ma60.iloc[i]),

        # 20MA 斜率：用近 5 日 MA20 變化率，避免單日雜訊（P6 盤整濾網用）
        "ma20_slope_5d": _r((ma20.iloc[i] / ma20.iloc[-6] - 1) * 100),

        # 乖離率 (P13)
        "bias20_pct": _r((today / ma20.iloc[i] - 1) * 100),

        # 量 (P7)
        "volume": int(v.iloc[i]),
        "vol_ma5": _r(vol_ma5.iloc[i], 0),
        "vol_ratio": _r(v.iloc[i] / vol_ma5.iloc[i]),

        # 破位 (P6 / P8)
        "days_below_ma20": days_below,
        "below_ma20": bool(today < ma20.iloc[i]),
        "black_k_break": bool(
            today < ma20.iloc[i] and (today / prev_close - 1) * 100 < black_k_drop
        ),

        # 資金曲線 (V6-2 / V6-2a)
        "high_60d": _r(high_60),
        "drawdown_from_60d_high": _r((today / high_60 - 1) * 100),
        "ann_vol": _r(ann_vol, 3),

        # 跳空 (P11)
        "gap_pct": _r(gap_pct),

        # 移動止盈參考 (P10)
        "below_ma5": bool(today < ma5.iloc[i]),
        "below_ma10": bool(today < ma10.iloc[i]),
        "below_ma60": bool(today < ma60.iloc[i]),
    }


def is_limit_down(df: pd.DataFrame, limit_pct: float, tol: float = 0.003) -> bool:
    """A 股跌停判定 —— 跌停時掛不出去，止損規則會失效，必須另外標記。"""
    if len(df) < 2:
        return False
    today, prev = df["close"].iloc[-1], df["close"].iloc[-2]
    floor_price = round(prev * (1 - limit_pct), 2)
    return today <= floor_price * (1 + tol)
