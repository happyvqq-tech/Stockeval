"""推薦層 —— 把指標快照打成 0~100 分，並給出進場/停損/目標價。

跟 core/rules.py 的分工：
    rules.py      已經持有部位 → 該不該出場（防守）
    recommend.py  還沒有部位   → 該不該進場、排序誰優先（進攻）

一樣不分市場，市場差異只透過 config/markets.yaml 的 cost_bps 進來：
成本高的市場（台股 58.5bps）同樣訊號要打折，因為來回摩擦吃掉更多報酬。
"""

from __future__ import annotations

from config import market_cfg, rules as rules_cfg


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _trend(m) -> tuple[float, str]:
    """均線多頭排列：ma5 > ma10 > ma20 > ma60，滿足幾段算幾分。"""
    ma = [m.get("ma5"), m.get("ma10"), m.get("ma20"), m.get("ma60")]
    if any(x is None for x in ma):
        return 0.0, "均線資料不足"
    ok = sum(1 for a, b in zip(ma, ma[1:]) if a > b)
    tags = ["空頭排列", "均線糾結", "初步轉強", "完全多頭排列"]
    return ok / 3, tags[ok]


def _momentum(m) -> tuple[float, str]:
    """站上 20MA 給一半，20MA 斜率向上給另一半。"""
    above = 0.0 if m.get("below_ma20", True) else 0.5
    slope = m.get("ma20_slope_5d") or 0.0
    s = _clamp(slope / 2.0) * 0.5            # 5 日內 20MA 漲 2% 以上即滿分
    desc = f"{'站上' if above else '跌破'}20MA、20MA 5日斜率 {slope}%"
    return above + s, desc


def _volume(m) -> tuple[float, str]:
    """漲要有量、跌不能有量。"""
    vr, chg = m.get("vol_ratio"), m.get("chg_pct")
    if vr is None or chg is None:
        return 0.5, "量能資料不足"
    if chg > 0:
        return _clamp((vr - 0.8) / 1.2), f"上漲量比 {vr}"
    if vr >= 2.0:
        return 0.0, f"下跌爆量，量比 {vr}"
    return _clamp(1.2 - vr) * 0.6, f"下跌量比 {vr}（縮量下跌較無傷）"


def _position(m) -> tuple[float, str]:
    """位階：乖離過大要扣分，深度回撤也要扣分。"""
    bias = m.get("bias20_pct")
    dd = m.get("drawdown_from_60d_high")
    if bias is None or dd is None:
        return 0.5, "位階資料不足"
    # 乖離 0~8% 最理想；負乖離或 >15% 都扣
    b = 1.0 if 0 <= bias <= 8 else _clamp(1 - abs(bias - 4) / 14)
    # 回撤 0~-10% 可接受，-25% 以下歸零
    d = _clamp(1 + dd / 25)
    return (b + d) / 2, f"乖離 {bias}%、自 60 日高回撤 {dd}%"


def _volatility(m) -> tuple[float, str]:
    """年化波動 20%~45% 最適合波段；太低沒空間，太高留不住。"""
    av = m.get("ann_vol")
    if av is None:
        return 0.5, "波動率資料不足"
    if av < 0.15:
        return _clamp(av / 0.15) * 0.6, f"年化波動 {av}，過於牛皮"
    if av <= 0.45:
        return 1.0, f"年化波動 {av}，適合波段"
    return _clamp(1 - (av - 0.45) / 0.55), f"年化波動 {av}，偏高需縮小部位"


_FACTORS = {
    "trend": _trend,
    "momentum": _momentum,
    "volume": _volume,
    "position": _position,
    "volatility": _volatility,
}


def score(m: dict, *, cfg: dict | None = None) -> dict:
    """單一標的的進場評分。輸入同樣是 indicators.compute() 的輸出。"""
    sc = (rules_cfg() if cfg is None else cfg)["score"]
    mkt = market_cfg(m["market"])

    breakdown = []
    total = 0.0
    for name, fn in _FACTORS.items():
        weight = sc[name]
        ratio, desc = fn(m)
        pts = round(ratio * weight, 1)
        total += pts
        breakdown.append(
            {"factor": name, "weight": weight, "points": pts, "detail": desc}
        )

    # 交易成本折價：成本越高，同樣的訊號越不值得做
    penalty = 0.0
    if mkt["cost_bps"] > sc["cost_penalty_bps"]:
        penalty = round(min(5.0, (mkt["cost_bps"] - sc["cost_penalty_bps"]) / 10), 1)
        total -= penalty

    total = round(_clamp(total, 0, 100), 1)

    rating, label = "AVOID", "迴避"
    for tier in sc["ratings"]:
        if total >= tier["min"]:
            rating, label = tier["rating"], tier["label"]
            break

    close = m["close"]
    ma20 = m.get("ma20") or close * 0.92
    stop = round(min(ma20, close * 0.92), 2)      # 停損取 20MA 與 -8% 較低者
    risk = (close - stop) / close if close else 0
    target = round(close * (1 + 2 * risk), 2)     # 預設報酬風險比 2:1

    notes = []
    if penalty:
        notes.append(f"{mkt['name']}來回成本 {mkt['cost_bps']}bps，評分已扣 {penalty} 分")
    if mkt["settlement"] != "T+0":
        notes.append(f"{mkt['settlement']} 交割：當日買進無法當日停損")
    if risk * 100 > 10:
        notes.append(f"停損距離 {risk * 100:.1f}%，單筆風險偏大，部位需縮小")

    return {
        "symbol": m.get("symbol", ""),
        "market": m["market"],
        "as_of": m["as_of"],
        "close": close,
        "score": total,
        "rating": rating,
        "rating_label": label,
        "cost_penalty": penalty,
        "breakdown": breakdown,
        "entry": close,
        "stop": stop,
        "target": target,
        "risk_pct": round(risk * 100, 2),
        "notes": notes,
    }


def rank(metrics: list[dict], *, top: int | None = None, min_score: float = 0.0,
         cfg: dict | None = None) -> list[dict]:
    """對一籃子標的評分並排序，分數高的在前。"""
    out = [score(m, cfg=cfg) for m in metrics]
    out = [x for x in out if x["score"] >= min_score]
    out.sort(key=lambda x: x["score"], reverse=True)
    return out[:top] if top else out
