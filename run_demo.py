"""煙霧測試：用合成資料跑完 indicators → rules → recommend 全流程，不需要網路。

真實用法：
    from data.base import get_adapter
    df = get_adapter("TW").ohlcv("2330", "2025-01-01")
"""
import numpy as np, pandas as pd

from core.indicators import compute
from core.recommend import score
from core.rules import evaluate


def fake(n=180, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.02, n)))
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, .004, n)),
        "high": close * (1 + abs(rng.normal(0, .008, n))),
        "low":  close * (1 - abs(rng.normal(0, .008, n))),
        "close": close,
        "volume": rng.integers(5_000, 50_000, n),
    }, index=idx)


def main(seed=7):
    df = fake(seed=seed)
    for mkt in ["TW", "US", "CN"]:
        m = compute(df, symbol="TEST", market=mkt)
        out = evaluate(m, unrealized_pct=25)
        rec = score(m)
        print(f"--- {mkt} | as_of {out['as_of']} | 成本 {out['cost_bps']}bps ---")
        print(f"  出場：{out['action_label']}（嚴重度 {out['severity_score']}）")
        print("  觸發:", "、".join(out["triggered"]) or "無")
        for r in out["rules"]:
            if r["triggered"]:
                print(f"    [{r['code']}] {r['detail']}")
        print(f"  進場：{rec['score']} 分 / {rec['rating_label']}"
              f"（停損 {rec['stop']}、目標 {rec['target']}）")
        for n_ in out["structural_notes"]:
            print("  ", n_)


if __name__ == "__main__":
    main()
