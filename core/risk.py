"""單筆部位的風險尺度 —— 進場算停損價、出場判斷該不該認賠，共用同一把尺。

獨立成一個模組，是因為 core/recommend.py（進場）與 core/rules.py（出場）
都要用它。兩邊各算各的，就會出現「進場端告訴你停損 139，出場端卻從來
不檢查」這種自打嘴巴的情況 —— 那正是這個模組要解決的問題。

一樣不分市場：沒有任何 if market，門檻全部從 config 讀（鐵則 1、2）。
"""

from __future__ import annotations

import math

from config import rules as rules_cfg

# 年化波動換算日波動的基準。這是定義不是可調門檻，所以留在程式裡。
TRADING_DAYS = 252


def max_loss_pct(ann_vol: float | None, *, cfg: dict | None = None) -> float:
    """這筆部位最多可以賠幾 %（回傳正數）。

    用日波動的 N 倍，而不是固定百分比：固定 8% 對年化波動 0.15 的標的
    等於 9 個標準差（幾乎不可能觸發，形同沒有停損），對年化波動 0.6 的
    標的又只有 2 個標準差（正常盤整就被洗掉）。

    再用上下限夾住：上限是「單筆最多願意賠多少」，下限避免停損太緊被
    雜訊掃到。
    """
    c = (rules_cfg() if cfg is None else cfg)["stop"]
    if ann_vol is None or ann_vol <= 0:
        return float(c["max_loss_pct"])      # 波動算不出來時退回固定上限
    daily = ann_vol / math.sqrt(TRADING_DAYS)
    scaled = c["vol_sigma"] * daily * 100
    return float(max(c["min_loss_pct"], min(c["max_loss_pct"], scaled)))


def stop_price(close: float, ann_vol: float | None,
               *, cfg: dict | None = None) -> tuple[float, float]:
    """回傳 (停損價, 可承受虧損%)。

    刻意只看風險上限，不把 20MA 混進來 —— 這個數字只回答一個問題：
    「最多賠多少就要走」。

    舊版取 min(20MA, 虧損上限)，兩件事混在一起會自相矛盾：20MA 離現價
    很遠時（例如 -15%），停損價會落在虧損上限之外，變成「進場端說停損
    -15%、出場端的 P0 卻在 -5.7% 就要求出場」。

    跌破 20MA 這個技術面出場點沒有消失，由 P6／P8 負責，跟資金管理的
    底線分開處理。
    """
    loss = max_loss_pct(ann_vol, cfg=cfg)
    return round(close * (1 - loss / 100), 2), loss
