"""規則判定 —— 只有一份，不分市場。

輸入是 core/indicators.compute() 的扁平 dict，外加持倉資訊。
市場差異全部從 config/markets.yaml 讀進來，這個檔案裡沒有任何
`if market == "TW"`。新增市場只要改 YAML。

輸出是一份 JSON-safe 的 dict，這份才送給 LLM。LLM 不做任何計算、
不做任何判斷，只負責把下面的 rules / action / structural_notes 寫成人話。
"""

from __future__ import annotations

from config import market_cfg, rules as rules_cfg
from core import risk

# 規則代號 → 中文名。改名不影響邏輯，只影響輸出可讀性。
NAMES = {
    "P0": "硬性停損",
    "P6": "黑K跌破20MA",
    "P7": "爆量下跌",
    "P8": "連續收在20MA之下",
    "P10": "移動止盈",
    "P11": "向下跳空",
    "P13": "乖離率過大",
    "V6-2": "自60日高點回撤",
    "V6-2a": "高波動股回撤（放寬門檻）",
}


def _hit(code, triggered, detail, severity):
    return {
        "code": code,
        "name": NAMES.get(code, code),
        "triggered": bool(triggered),
        "severity": int(severity) if triggered else 0,
        "detail": detail,
    }


def evaluate(
    m: dict,
    *,
    unrealized_pct: float = 0.0,
    limit_down: bool = False,
    adjusted: bool = True,
    cfg: dict | None = None,
) -> dict:
    """對單一標的的指標快照跑完全部規則。

    m              core/indicators.compute() 的輸出
    unrealized_pct 目前未實現損益（%），正數為獲利；移動止盈只在有獲利時啟動
    limit_down     今日是否跌停（A股）。跌停掛不出去，止損實際上失效
    adjusted       原始資料是否已還原權息。False 時所有價格規則都不可信
    """
    r = rules_cfg() if cfg is None else cfg
    mkt = market_cfg(m["market"])

    results: list[dict] = []
    notes: list[str] = []

    # ---------- P0 硬性停損（虧損超過可承受範圍）----------
    # 放第一條是刻意的：其他規則看的都是「技術面相對位置」（相對 20MA、
    # 相對 60 日高點），沒有一條看得到你的成本。60 日高點不是你的買進價 ——
    # 在半山腰接刀的話，跌掉三成也可能沒有任何規則會叫。
    max_loss = risk.max_loss_pct(m.get("ann_vol"), cfg=r)
    p0 = unrealized_pct <= -max_loss
    results.append(_hit(
        "P0", p0,
        f"未實現 {unrealized_pct}%，"
        + (f"已跌破停損線 -{max_loss:.1f}%" if p0 else f"停損線 -{max_loss:.1f}%")
        + f"（依年化波動 {m.get('ann_vol')} 換算）",
        r["stop"]["severity"]))

    # ---------- P6 黑K跌破 20MA ----------
    c = r["p6"]
    slope = m.get("ma20_slope_5d")
    flat = slope is not None and slope > c["ma20_slope_min"]
    vol_ok = True
    if mkt["p6_require_volume"]:
        vr = m.get("vol_ratio")
        vol_ok = vr is not None and vr >= c["vol_ratio_min"]

    p6 = bool(m["black_k_break"]) and not flat and vol_ok
    why = []
    if not m["black_k_break"]:
        why.append("未同時滿足跌破20MA與單日跌幅")
    if flat:
        why.append(f"20MA斜率 {slope}% 仍屬盤整，視為假破位")
    if not vol_ok:
        why.append(f"量比 {m.get('vol_ratio')} < {c['vol_ratio_min']}，本市場需量能確認")
    results.append(
        _hit(
            "P6",
            p6,
            f"收 {m['close']} 跌破 20MA {m['ma20']}、單日 {m['chg_pct']}%、"
            f"量比 {m.get('vol_ratio')}" if p6 else "；".join(why),
            c["severity"],
        )
    )

    # ---------- P7 爆量下跌 ----------
    c = r["p7"]
    vr, chg = m.get("vol_ratio"), m.get("chg_pct")
    p7 = vr is not None and chg is not None and vr >= c["vol_ratio"] and chg <= c["chg_pct_max"]
    results.append(
        _hit("P7", p7,
             f"量比 {vr}（門檻 {c['vol_ratio']}）、當日 {chg}%"
             if p7 else f"量比 {vr}、當日 {chg}%，未達爆量下跌",
             c["severity"])
    )

    # ---------- P8 連續收在 20MA 之下 ----------
    c = r["p8"]
    d = m["days_below_ma20"]
    p8 = d >= c["days_below"]
    results.append(
        _hit("P8", p8,
             f"已連續 {d} 日收在 20MA 之下（門檻 {c['days_below']} 日）",
             c["severity"])
    )

    # ---------- P10 移動止盈（有獲利才啟動）----------
    c = r["p10"]
    key = f"below_ma{c['ma']}"
    has_profit = unrealized_pct >= c["min_unrealized_pct"]
    p10 = has_profit and bool(m.get(key))
    ma_key = f"ma{c['ma']}"
    if p10:
        detail = f"未實現 +{unrealized_pct}% 且跌破 {c['ma']}MA {m.get(ma_key)}，移動止盈啟動"
    elif not has_profit:
        detail = f"未實現 {unrealized_pct}% < {c['min_unrealized_pct']}%，移動止盈未啟動"
    else:
        detail = f"仍站穩 {c['ma']}MA"
    results.append(_hit("P10", p10, detail, c["severity"]))

    # ---------- P11 向下跳空 ----------
    c = r["p11"]
    gap = m.get("gap_pct")
    p11 = gap is not None and gap <= c["gap_pct"]
    results.append(
        _hit("P11", p11,
             f"開盤跳空 {gap}%（門檻 {c['gap_pct']}%）"
             if p11 else f"跳空 {gap}%，未達門檻",
             c["severity"])
    )

    # ---------- P13 乖離率過大 ----------
    c = r["p13"]
    bias = m.get("bias20_pct")
    p13 = bias is not None and bias >= c["bias_pct"]
    results.append(
        _hit("P13", p13,
             f"正乖離 {bias}% ≥ {c['bias_pct']}%，過熱"
             if p13 else f"乖離 {bias}%，未達過熱門檻 {c['bias_pct']}%",
             c["severity"])
    )

    # ---------- V6-2 / V6-2a 資金曲線回撤 ----------
    dd = m.get("drawdown_from_60d_high")
    av = m.get("ann_vol")
    hi_vol = av is not None and av >= r["v6_2a"]["ann_vol_min"]
    c = r["v6_2a"] if hi_vol else r["v6_2"]
    code = "V6-2a" if hi_vol else "V6-2"
    v6 = dd is not None and dd <= c["drawdown_pct"]
    results.append(
        _hit(code, v6,
             f"自 60 日高 {m.get('high_60d')} 回撤 {dd}%"
             f"（年化波動 {av}，門檻 {c['drawdown_pct']}%）",
             c["severity"])
    )

    # ---------- 嚴重度加總 → 動作 ----------
    score = sum(x["severity"] for x in results)
    action, label = "HOLD", "續抱"
    for tier in r["actions"]:
        if score >= tier["min_score"]:
            action, label = tier["action"], tier["label"]
            break

    # ---------- 結構性提示 ----------
    settlement = mkt["settlement"]
    if settlement != "T+0" and action != "HOLD":
        notes.append(
            f"{mkt['name']} {settlement}：訊號於 {m['as_of']} 產生，"
            f"最快下一交易日才能執行，需承擔隔日跳空風險。"
        )
    if limit_down:
        notes.append("今日跌停，賣單無法成交 —— 止損規則實際上失效，改列為隔日優先處理。")
        if action == "HOLD":
            action, label = "WATCH", "跌停鎖死，隔日優先處理"
    if not adjusted:
        notes.append(
            "原始資料未還原權息：除權息日的價格跳空會被誤判為跌破均線，"
            "本次所有價格類規則（P6/P8/P10/P11）結果不可信。"
        )
    if p0:
        notes.append(
            f"已跌破停損線：可承受虧損 -{max_loss:.1f}% 是用年化波動 "
            f"{m.get('ann_vol')} 換算的（{r['stop']['vol_sigma']} 個日標準差，"
            f"夾在 -{r['stop']['min_loss_pct']}% ~ -{r['stop']['max_loss_pct']}% 之間）。"
            "停損的意義在於執行，不在於再等一根 K 棒。"
        )
    if action != "HOLD":
        notes.append(
            f"來回成本 {mkt['cost_bps']} bps —— "
            f"若此次減碼後三日內回補，成本會吃掉 {mkt['cost_bps'] / 100:.2f}% 的報酬。"
        )
    if mkt["has_price_limit"] and action in ("EXIT_ALL", "REDUCE_50"):
        notes.append(f"{mkt['name']}有漲跌停（±{mkt['limit_pct'] * 100:.0f}%），大量出場可能觸及跌停排隊。")

    triggered = [x["code"] for x in results if x["triggered"]]

    return {
        "symbol": m.get("symbol", ""),
        "market": m["market"],
        "as_of": m["as_of"],
        "cost_bps": mkt["cost_bps"],
        "settlement": settlement,
        "unrealized_pct": unrealized_pct,
        "severity_score": score,
        "action": action,
        "action_label": label,
        "triggered": triggered,
        "rules": results,
        "structural_notes": notes,
    }
