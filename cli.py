"""指令列介面。

    python cli.py doctor                      環境自我診斷（先跑這個）
    python cli.py demo                        不用網路的煙霧測試
    python cli.py rec    --market TW          掃描整個股票池並排序
    python cli.py rec    --market TW --symbols 2330,2317
    python cli.py check  --market US --symbol AAPL -u 25
    python cli.py cache  --clear

加 --json 輸出原始 JSON，那份就是要餵給 LLM 的東西。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from datetime import date, timedelta

from core.indicators import compute
from core.recommend import rank, score
from core.rules import evaluate

BAR = "─" * 68


def _w(text) -> int:
    """顯示寬度：中文是全形佔兩欄，用 len() 對齊會歪掉。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(text))


def _pad(text, n: int) -> str:
    return f"{text}{' ' * max(0, n - _w(text))}"


def _rpad(text, n: int) -> str:
    return f"{' ' * max(0, n - _w(text))}{text}"


def _err(msg):
    print(msg, file=sys.stderr)


def _default_start() -> str:
    return str(date.today() - timedelta(days=420))      # 約 280 個交易日


def _adapter(market: str):
    from data.base import get_adapter
    return get_adapter(market)


def _load(ad, symbol: str, start: str, no_cache: bool):
    df = ad.ohlcv(symbol, start, use_cache=not no_cache)
    if len(df) < 60:
        raise ValueError(f"只取到 {len(df)} 筆，不足 60 筆")
    return df


# --------------------------------------------------------------------------
def cmd_rec(a) -> int:
    from data import universe

    try:
        names = universe.names(a.market)        # 有名稱就帶出來，方便閱讀
    except FileNotFoundError:
        names = {}

    if a.symbols:
        symbols = [s.strip() for s in a.symbols.split(",") if s.strip()]
    else:
        symbols = universe.symbols(a.market)
        _err(f"未指定 --symbols，掃描 {a.market} 股票池共 {len(symbols)} 檔")

    ad = _adapter(a.market)
    start = a.start or _default_start()
    metrics, failed, cached = [], [], 0

    for n, sym in enumerate(symbols, 1):
        if not a.json and len(symbols) > 5:
            _err(f"  [{n}/{len(symbols)}] {sym}")
        try:
            df = _load(ad, sym, start, a.no_cache)
            metrics.append(compute(df, symbol=sym, market=a.market))
            cached += bool(getattr(ad, "from_cache", False))
        except Exception as e:                  # 單一標的失敗不中斷整批
            failed.append({"symbol": sym, "error": str(e)})

    out = rank(metrics, top=a.top, min_score=a.min_score)

    if a.json:
        print(json.dumps({"market": a.market, "ranked": out, "failed": failed},
                         ensure_ascii=False, indent=2))
        return 0 if out else 1

    if not out:
        _err("\n沒有任何標的取得足夠資料。先跑 python cli.py doctor 檢查環境。")
        for f in failed[:10]:
            _err(f"  ✗ {f['symbol']}：{f['error']}")
        return 1

    print(f"\n{a.market} 推薦排序　as_of {out[0]['as_of']}"
          f"　成功 {len(out)}/{len(symbols)}　快取命中 {cached}\n{BAR}")
    print(_pad("代號", 9) + _pad("名稱", 14) + _rpad("分數", 6) + "  "
          + _pad("評級", 10) + _rpad("現價", 10) + _rpad("停損", 10) + _rpad("目標", 10))
    for x in out:
        print(_pad(x["symbol"], 9) + _pad(names.get(x["symbol"], ""), 14)
              + _rpad(x["score"], 6) + "  " + _pad(x["rating_label"], 10)
              + _rpad(f"{x['entry']:.2f}", 10) + _rpad(f"{x['stop']:.2f}", 10)
              + _rpad(f"{x['target']:.2f}", 10))

    if not ad.adjusted:
        print("\n  ⚠ 資料未還原權息（鐵則 4）：除權息跳空會被誤判，本次結果不可信。")
    if a.verbose:
        for x in out:
            print(f"\n{x['symbol']} {names.get(x['symbol'], '')} "
                  f"{x['score']} 分（{x['rating_label']}）　風險 {x['risk_pct']}%")
            for b in x["breakdown"]:
                print(f"  {b['factor']:<11}{b['points']:>5}/{b['weight']:<4}{b['detail']}")
            for n_ in x["notes"]:
                print(f"  ⚠ {n_}")
    if failed:
        print(f"\n  取得失敗 {len(failed)} 檔：")
        for f in failed[:10]:
            print(f"    ✗ {f['symbol']}：{f['error']}")
        if len(failed) > 10:
            print(f"    …另外 {len(failed) - 10} 檔")
    print()
    return 0


# --------------------------------------------------------------------------
def cmd_check(a) -> int:
    ad = _adapter(a.market)
    df = _load(ad, a.symbol, a.start or _default_start(), a.no_cache)
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

    rec = out["recommendation"]
    print(f"\n{a.market}:{a.symbol}　as_of {out['as_of']}　收 {m['close']}"
          f"　未實現 {a.unrealized}%\n{BAR}")
    print(f"出場：{out['action_label']}（{out['action']}，嚴重度 {out['severity_score']}）")
    print(f"觸發：{'、'.join(out['triggered']) or '無'}")
    for r in out["rules"]:
        print(f"  {'●' if r['triggered'] else '○'} [{r['code']:<5}] {r['name']}：{r['detail']}")
    print(f"\n進場：{rec['score']} 分 / {rec['rating_label']}"
          f"　停損 {rec['stop']}　目標 {rec['target']}　風險 {rec['risk_pct']}%")
    for n_ in out["structural_notes"]:
        print(f"  ⚠ {n_}")
    print()
    return 0


# --------------------------------------------------------------------------
def cmd_doctor(a) -> int:
    """把「為什麼跑不起來」一次講清楚，不用你自己猜。"""
    import importlib
    import os

    from data import cache, universe

    ok = True
    print(f"\n環境診斷\n{BAR}")

    print("套件：")
    for mod, why in [("pandas", "必要"), ("yaml", "必要"),
                     ("requests", "台股"), ("yfinance", "美股"), ("akshare", "A股")]:
        try:
            importlib.import_module(mod)
            print(f"  ✓ {mod:<10}{why}")
        except ImportError:
            print(f"  ✗ {mod:<10}{why} —— pip install {'pyyaml' if mod == 'yaml' else mod}")
            ok = ok and why != "必要"

    print("\n憑證：")
    tok = os.getenv("FINMIND_TOKEN", "")
    print(f"  {'✓' if tok else '✗'} FINMIND_TOKEN "
          f"{'已設定（' + tok[:6] + '…）' if tok else '未設定 —— 台股會退回未還原股價'}")

    print("\n快取：")
    print(f"  路徑 {cache.root().resolve()}　{'啟用' if cache.enabled() else '已停用'}")
    try:
        cache.root().mkdir(parents=True, exist_ok=True)
        n = len(list(cache.root().rglob("*.csv")))
        print(f"  ✓ 可寫入，目前 {n} 個檔案")
    except Exception as e:
        print(f"  ✗ 無法寫入：{e}")
        ok = False

    print("\n股票池：")
    for mkt in universe.available():
        print(f"  ✓ {mkt}：{len(universe.load(mkt))} 檔")

    print("\n連線（每個市場實際抓一檔）：")
    probes = [("TW", "2330"), ("US", "AAPL"), ("CN", "600519")]
    for mkt, sym in probes:
        try:
            ad = _adapter(mkt)
            df = ad.ohlcv(sym, _default_start(), use_cache=False)
            if len(df):
                flag = "" if ad.adjusted else "（未還原權息）"
                print(f"  ✓ {mkt} {sym}：{len(df)} 筆，最新 {df.index[-1].date()}{flag}")
            else:
                print(f"  ✗ {mkt} {sym}：沒有取得資料"
                      f"（網路被擋、代號有誤、或該來源需要金鑰）")
                ok = False
        except Exception as e:
            print(f"  ✗ {mkt} {sym}：{type(e).__name__} {str(e)[:90]}")
            ok = False

    print(f"\n{'全部正常，可以開始用了。' if ok else '有項目未通過，請依上面的訊息處理。'}\n")
    return 0 if ok else 1


# --------------------------------------------------------------------------
def cmd_cache(a) -> int:
    from data import cache

    if a.clear:
        n = cache.clear(a.market)
        print(f"已清除 {n} 個快取檔案")
        return 0
    base = cache.root()
    files = sorted(base.rglob("*.csv")) if base.exists() else []
    size = sum(f.stat().st_size for f in files)
    print(f"\n快取 {base.resolve()}　{len(files)} 檔　{size / 1024:.1f} KB")
    for f in files[:20]:
        print(f"  {f.parent.name}/{f.stem:<10}{cache.age_hours(f.parent.name, f.stem):.1f} 小時前")
    if len(files) > 20:
        print(f"  …另外 {len(files) - 20} 個")
    print()
    return 0


def cmd_demo(a) -> int:
    import run_demo
    run_demo.main()
    return 0


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="stockcore",
                                description="三市場共用的股票推薦 / 持倉檢查")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--market", required=True, choices=["TW", "US", "CN"])
        sp.add_argument("--start", default=None, help="起始日 YYYY-MM-DD，預設約 420 天前")
        sp.add_argument("--no-cache", action="store_true", help="略過本地快取，強制重抓")
        sp.add_argument("--json", action="store_true", help="輸出原始 JSON（給 LLM 用）")

    sp = sub.add_parser("rec", help="推薦排序；不給 --symbols 就掃整個股票池")
    common(sp)
    sp.add_argument("--symbols", default=None, help="逗號分隔，例：2330,2317,2454")
    sp.add_argument("--top", type=int, default=None)
    sp.add_argument("--min-score", dest="min_score", type=float, default=0.0)
    sp.add_argument("-v", "--verbose", action="store_true", help="顯示各因子分數")
    sp.set_defaults(func=cmd_rec)

    sp = sub.add_parser("check", help="持倉檢查")
    common(sp)
    sp.add_argument("--symbol", required=True)
    sp.add_argument("-u", "--unrealized", type=float, default=0.0, help="未實現損益 %%")
    sp.set_defaults(func=cmd_check)

    sp = sub.add_parser("doctor", help="環境自我診斷")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("cache", help="檢視或清除本地快取")
    sp.add_argument("--clear", action="store_true")
    sp.add_argument("--market", default=None, choices=["TW", "US", "CN"])
    sp.set_defaults(func=cmd_cache)

    sp = sub.add_parser("demo", help="不用網路的煙霧測試")
    sp.set_defaults(func=cmd_demo)

    a = p.parse_args(argv)
    try:
        return a.func(a)
    except KeyboardInterrupt:
        _err("\n已中斷")
        return 130
    except Exception as e:
        _err(f"錯誤：{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
