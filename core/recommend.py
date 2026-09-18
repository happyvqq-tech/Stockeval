"""推薦層 —— 把指標快照打成 0~100 分，並給出進場/停損/目標價。

跟 core/rules.py 的分工：
    rules.py      已經持有部位 → 該不該出場（防守）
    recommend.py  還沒有部位   → 該不該進場、排序誰優先（進攻）

一樣不分市場：市場差異只透過 config/markets.yaml 的 cost_bps 進來，
評分門檻全部在 config/rules.yaml 的 score.thresholds，程式裡不寫死（鐵則 2）。
"""

from __future__ import annotations

from config import market_cfg, rules as rules_cfg


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _trend(m, t, unknown) -> tuple[float, str]:
    """均線多頭排列：ma5 > ma10 > ma20 > ma60，滿足幾段算幾分。"""
    ma = [m.get("ma5"), m.get("ma10"), m.get("ma20"), m.get("ma60")]
    if any(x is None for x in ma):
        return unknown, "均線資料不足"
    ok = sum(1 for a, b in zip(ma, ma[1:]) if a > b)
    return ok / 3, ["空頭排列", "均線糾結", "初步轉強", "完全多頭排列"][ok]


def _momentum(m, t, unknown) -> tuple[float, str]:
    """站上 20MA 拿一半，20MA 斜率向上拿另一半。"""
    c = t["momentum"]
    above = 0.0 if m.get("below_ma20", True) else c["above_ma20_credit"]
    slope = m.get("ma20_slope_5d") or 0.0
    s = _clamp(slope / c["slope_full_pct"]) * (1 - c["above_ma20_credit"])
    return above + s, f"{'站上' if above else '跌破'}20MA、20MA 5日斜率 {slope}%"


def _volume(m, t, unknown) -> tuple[float, str]:
    """漲要有量、跌不能有量。"""
    c = t["volume"]
    vr, chg = m.get("vol_ratio"), m.get("chg_pct")
    if vr is None or chg is None:
        return unknown, "量能資料不足"
    if chg > 0:
        span = c["up_vol_full"] - c["up_vol_floor"]
        return _clamp((vr - c["up_vol_floor"]) / span), f"上漲量比 {vr}"
    if vr >= c["dump_vol_ratio"]:
        return 0.0, f"下跌爆量，量比 {vr}"
    return _clamp(c["dry_vol_ratio"] - vr) * c["dry_credit"], \
        f"下跌量比 {vr}（縮量下跌較無傷）"


def _position(m, t, unknown) -> tuple[float, str]:
    """位階：乖離過大要扣分，深度回撤也要扣分。"""
    c = t["position"]
    bias, dd = m.get("bias20_pct"), m.get("drawdown_from_60d_high")
    if bias is None or dd is None:
        return unknown, "位階資料不足"
    lo, hi = c["bias_ideal_lo"], c["bias_ideal_hi"]
    if lo <= bias <= hi:
        b = 1.0
    else:
        b = _clamp(1 - abs(bias - (lo + hi) / 2) / c["bias_tolerance"])
    d = _clamp(1 + dd / abs(c["drawdown_zero_pct"]))
    return (b + d) / 2, f"乖離 {bias}%、自 60 日高回撤 {dd}%"


def _volatility(m, t, unknown) -> tuple[float, str]:
    """波動要適中：太低沒空間，太高留不住。"""
    c = t["volatility"]
    av = m.get("ann_vol")
    if av is None:
        return unknown, "波動率資料不足"
    if av < c["too_low"]:
        return _clamp(av / c["too_low"]) * c["low_credit"], f"年化波動 {av}，過於牛皮"
    if av <= c["band_hi"]:
        return 1.0, f"年化波動 {av}，適合波段"
    span = c["zero_at"] - c["band_hi"]
    return _clamp(1 - (av - c["band_hi"]) / span), f"年化波動 {av}，偏高需縮小部位"


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
    t, unknown = sc["thresholds"], sc["unknown"]
    mkt = market_cfg(m["market"])

    breakdown, total = [], 0.0
    for name, fn in _FACTORS.items():
        weight = sc[name]
        ratio, desc = fn(m, t, unknown)
        pts = round(ratio * weight, 1)
        total += pts
        breakdown.append({"factor": name, "weight": weight, "points": pts, "detail": desc})

    # 交易成本折價：成本越高，同樣的訊號越不值得做
    penalty = 0.0
    if mkt["cost_bps"] > sc["cost_penalty_bps"]:
        over = mkt["cost_bps"] - sc["cost_penalty_bps"]
        penalty = round(min(sc["cost_penalty_max"], over / sc["cost_penalty_per_bps"]), 1)
        total -= penalty

    total = round(_clamp(total, 0, 100), 1)

    rating, label = sc["ratings"][-1]["rating"], sc["ratings"][-1]["label"]
    for tier in sc["ratings"]:
        if total >= tier["min"]:
            rating, label = tier["rating"], tier["label"]
            break

    e = sc["entry"]
    close = m["close"]
    floor = close * (1 - e["stop_max_loss_pct"] / 100)
    ma20 = m.get("ma20") or floor
    stop = round(min(ma20, floor), 2)              # 停損取 20MA 與最大容忍虧損較低者
    risk = (close - stop) / close if close else 0
    target = round(close * (1 + e["reward_risk"] * risk), 2)

    notes = []
    if penalty:
        notes.append(f"{mkt['name']}來回成本 {mkt['cost_bps']}bps，評分已扣 {penalty} 分")
    if mkt["settlement"] != "T+0":
        notes.append(f"{mkt['settlement']} 交割：當日買進無法當日停損")
    if risk * 100 > e["risk_warn_pct"]:
        notes.append(f"停損距離 {risk * 100:.1f}%，單筆風險偏大，部位需縮小")

    return {
        "symbol": m.get("symbol", ""),
        "market": m["market"],
        "as_of": m["as_of"],                       # 鐵則 5：每個輸出都帶 as_of
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
