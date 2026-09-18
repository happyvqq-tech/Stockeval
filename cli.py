"""指令列介面。

    python cli.py demo                                        不用網路，跑合成資料
    python cli.py rec    --market TW --symbols 2330,2317      推薦排序（進場）
    python cli.py check  --market US --symbol AAPL -u 25      持倉檢查（出場）

加 --json 可輸出原始 JSON，那份就是要餵給 LLM 的東西。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta

from core.indicators import compute
from core.recommend import rank, score
from core.rules import evaluate

BAR = "─" * 62


def _load(market: str, symbol: str, start: str):
    from data.base import get_adapter

    ad = get_adapter(market)
    df = ad.ohlcv(symbol, start)
    if len(df) < 60:
        raise ValueError(f"[{market}:{symbol}] 只取到 {len(df)} 筆，不足 60 筆")
    return ad, df


def _default_start() -> str:
    return str(date.today() - timedelta(days=420))   # 約 280 個交易日


def cmd_rec(a) -> int:
    symbols = [s.strip() for s in a.symbols.split(",") if s.strip()]
    metrics, failed = [], []
    for sym in symbols:
        try:
            _, df = _load(a.market, sym, a.start or _default_start())
            metrics.append(compute(df, symbol=sym, market=a.market))
        except Exception as e:                      # 單一標的失敗不該中斷整批
            failed.append((sym, str(e)))

    out = rank(metrics, top=a.top, min_score=a.min_score)
    if a.json:
        print(json.dumps({"ranked": out, "failed": failed}, ensure_ascii=False, indent=2))
        return 0

    print(f"\n{a.market} 推薦排序　as_of {out[0]['as_of'] if out else '-'}\n{BAR}")
    print(f"{'代號':<10}{'分數':>6}  {'評級':<8}{'現價':>9}{'停損':>9}{'目標':>9}")
    for x in out:
        print(f"{x['symbol']:<10}{x['score']:>6}  {x['rating_label']:<8}"
              f"{x['entry']:>9}{x['stop']:>9}{x['target']:>9}")
    if a.verbose:
        for x in out:
            print(f"\n{x['symbol']} {x['score']} 分（{x['rating_label']}）")
            for b in x["breakdown"]:
                print(f"  {b['factor']:<11}{b['points']:>5}/{b['weight']:<4}{b['detail']}")
            for n in x["notes"]:
                print(f"  ⚠ {n}")
    for sym, err in failed:
        print(f"  ✗ {sym}：{err}")
    print()
    return 0


def cmd_check(a) -> int:
    ad, df = _load(a.market, a.symbol, a.start or _default_start())
    m = compute(df, symbol=a.symbol, market=a.market)

    limit_down = False
    lp = ad.limit_pct(a.symbol)
    if lp:
        from core.indicators import is_limit_down
        limit_down = is_limit_down(df, lp)

    out = evaluate(m, unrealized_pct=a.unrealized,
                   limit_down=limit_down, adjusted=ad.adjusted)
    out["recommendation"] = score(m)

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"\n{a.market}:{a.symbol}　as_of {out['as_of']}　收 {m['close']}"
          f"　未實現 {a.unrealized}%\n{BAR}")
    print(f"建議：{out['action_label']}（{out['action']}，嚴重度 {out['severity_score']}）")
    print(f"觸發：{'、'.join(out['triggered']) or '無'}")
    for r in out["rules"]:
        mark = "●" if r["triggered"] else "○"
        print(f"  {mark} [{r['code']:<5}] {r['name']}：{r['detail']}")
    for n in out["structural_notes"]:
        print(f"  ⚠ {n}")
    print()
    return 0


def cmd_demo(a) -> int:
    import run_demo
    run_demo.main()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="stockcore", description="三市場共用的股票推薦 / 持倉檢查")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--market", required=True, choices=["TW", "US", "CN"])
        sp.add_argument("--start", default=None, help="起始日 YYYY-MM-DD，預設約 420 天前")
        sp.add_argument("--json", action="store_true", help="輸出原始 JSON（給 LLM 用）")

    sp = sub.add_parser("rec", help="推薦排序")
    common(sp)
    sp.add_argument("--symbols", required=True, help="逗號分隔，例：2330,2317,2454")
    sp.add_argument("--top", type=int, default=None)
    sp.add_argument("--min-score", dest="min_score", type=float, default=0.0)
    sp.add_argument("-v", "--verbose", action="store_true", help="顯示各因子分數")
    sp.set_defaults(func=cmd_rec)

    sp = sub.add_parser("check", help="持倉檢查")
    common(sp)
    sp.add_argument("--symbol", required=True)
    sp.add_argument("-u", "--unrealized", type=float, default=0.0, help="未實現損益 %%")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("demo", help="不用網路的煙霧測試")
    sp.set_defaults(func=cmd_demo)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except Exception as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
