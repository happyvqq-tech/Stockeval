"""把復盤結果整理成可以直接貼上的純文字摘要。

只放在網站層：CLI 本來就是純文字輸出，整段複製終端機就好。這是為了解決
網頁上結果很長、手機截圖會被截斷的問題 —— 按一下就拿到完整的統計區塊，
不含 40 列的個股明細（那部分對判讀評分品質沒有幫助）。
"""

from __future__ import annotations

EXAMPLES = 5        # 明細只保留分數最高與最低各幾檔，當作 sanity check


def _fmt(v, nd=2, width=0, suffix="", sign=False) -> str:
    """None 一律顯示成 —，不要印出 Python 的 None。"""
    if v is None:
        text = "—"
    else:
        text = f"{v:+.{nd}f}{suffix}" if sign else f"{v:.{nd}f}{suffix}"
    return text.rjust(width) if width else text


def _rows_block(rows: list[dict]) -> list[str]:
    priced = [r for r in rows if r.get("forward")]
    if not priced:
        return []

    def line(r):
        f = r["forward"]
        return (f"  {r['symbol']:<8}{r['score']:>6}"
                f"{_fmt(f['net_return_pct'], 2, 10, '%')}")

    out = ["", f"分數最高 {min(EXAMPLES, len(priced))} 檔"]
    out += [line(r) for r in priced[:EXAMPLES]]
    if len(priced) > EXAMPLES * 2:
        out += ["", f"分數最低 {EXAMPLES} 檔"]
        out += [line(r) for r in priced[-EXAMPLES:]]
    return out


def review_text(out: dict) -> str:
    """out 是 core.backtest.review() 的回傳值。"""
    s = out.get("summary") or {}
    lines = [f"{out['market']} 復盤　as_of {out['asof']} → until {out['until']}"]

    if not s.get("n"):
        lines.append("沒有任何標的同時取得 as_of 分數與之後的報酬。")
        if out.get("failed"):
            lines.append(f"取得失敗 {len(out['failed'])} 檔："
                         + "、".join(f["symbol"] for f in out["failed"][:10]))
        return "\n".join(lines)

    lines += [
        "",
        f"樣本 {s['n']}　平均淨報酬 {_fmt(s['avg_return_pct'], 2, 0, '%')}"
        f"　勝率 {_fmt(s['win_rate_pct'], 1, 0, '%')}"
        f"　分數/報酬相關係數 {_fmt(s['score_return_correlation'], 3)}",
    ]

    if s.get("benchmark_avg_return_pct") is not None:
        bench = f"對照基準（整池 {s['benchmark_n']} 檔等權）{_fmt(s['benchmark_avg_return_pct'], 2, 0, '%')}"
        if s.get("selected_is_whole_universe"):
            bench += "　超額 +0.00%（選了全部，結構使然）"
        else:
            bench += f"　超額 {s['excess_return_pct']:+.2f}%"
        lines.append(bench)

    lines.append(f"分數前半 {_fmt(s['avg_return_top_half_pct'], 2, 0, '%')}"
                 f"　後半 {_fmt(s['avg_return_bottom_half_pct'], 2, 0, '%')}")

    if s.get("score_buckets"):
        lines += ["", "分數分組"]
        for b in s["score_buckets"]:
            lo, hi = b["score_range"]
            lines.append(
                f"  {b['label']}  {b['n']:>2} 檔  分數 {lo:>5}~{hi:<5}"
                f"  報酬{_fmt(b['avg_return_pct'], 2, 8, '%')}"
                f"  超額 {b['excess_pct']:+.2f}%")

    if s.get("factor_stats"):
        # 欄位標題一律用 ASCII：中文是全形字，貼到不同的聊天視窗寬度算法
        # 不一樣，用全形當標題一定會歪。中文只留在最後一欄（歪了也不影響）。
        lines += ["", "逐因子",
                  f"  {'factor':<12}{'IC':>8}{'std':>8}{'std_ratio':>11}"
                  f"{'conc%':>8}  判定"]
        for name, v in s["factor_stats"].items():
            lines.append(
                f"  {name:<12}{_fmt(v['ic'], 3, 8, sign=True)}{_fmt(v['std'], 2, 8)}"
                f"{_fmt(v['std_ratio'], 3, 11)}{_fmt(v['concentration_pct'], 1, 8, '%')}"
                f"  {'可信' if v['reliable'] else '鑑別度不足'}")

    lines += _rows_block(out.get("rows") or [])

    if out.get("failed"):
        lines += ["", f"as_of 當時取得失敗 {len(out['failed'])} 檔："
                  + "、".join(f["symbol"] for f in out["failed"][:10])]

    return "\n".join(lines)
