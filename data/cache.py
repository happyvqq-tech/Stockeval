"""本地快取 —— FinMind 免費層有速率限制，掃描股票池沒快取一定被擋。

存成 CSV 而不是 parquet：日線資料一年才 250 列，CSV 夠小、不需要 pyarrow，
而且出問題時你可以直接打開來看。

路徑：環境變數 STOCKCORE_CACHE，預設 ./cache/<market>/<symbol>.csv
停用：環境變數 STOCKCORE_NO_CACHE=1，或 ohlcv(..., use_cache=False)
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd

DEFAULT_TTL_HOURS = 12.0


def enabled() -> bool:
    return os.getenv("STOCKCORE_NO_CACHE", "") not in ("1", "true", "True")


def root() -> Path:
    return Path(os.getenv("STOCKCORE_CACHE", "cache")).expanduser()


def _safe(symbol: str) -> str:
    """代號可能含 . 或 /（例如 BRK.B），轉成安全檔名。"""
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in symbol.strip())


def path(market: str, symbol: str) -> Path:
    return root() / market.upper() / f"{_safe(symbol)}.csv"


def meta_path(market: str, symbol: str) -> Path:
    return path(market, symbol).with_suffix(".meta.json")


def read_meta(market: str, symbol: str) -> dict:
    p = meta_path(market, symbol)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def age_hours(market: str, symbol: str) -> float | None:
    p = path(market, symbol)
    if not p.exists():
        return None
    fetched = read_meta(market, symbol).get("fetched_at")
    if fetched:
        return (time.time() - float(fetched)) / 3600
    return (time.time() - p.stat().st_mtime) / 3600


def load(market: str, symbol: str) -> pd.DataFrame | None:
    p = path(market, symbol)
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, index_col=0, parse_dates=True)
    except Exception:
        return None                      # 快取壞了就當沒有，不要讓它害整批失敗
    return df if len(df) else None


def save(market: str, symbol: str, df: pd.DataFrame,
         requested_start: date | None = None) -> None:
    if df is None or df.empty:
        return
    p = path(market, symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    merged = df
    old = load(market, symbol)
    if old is not None:                  # 與舊資料合併，新的蓋掉舊的
        merged = pd.concat([old, df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    tmp = p.with_suffix(".csv.tmp")      # 先寫暫存再 rename，避免中斷寫壞檔案
    merged.to_csv(tmp)
    tmp.replace(p)

    # 記下「當初請求的起始日」而不是「資料實際的第一筆」。請求日常落在週末或
    # 假日，實際第一筆一定比它晚；若拿實際第一筆去比對，快取將永遠判定為不足。
    prev = read_meta(market, symbol).get("requested_start")
    starts = [x for x in (prev, str(requested_start) if requested_start else None) if x]
    meta = {"fetched_at": time.time(),
            "requested_start": min(starts) if starts else None,
            "first_bar": str(merged.index.min().date()),
            "last_bar": str(merged.index.max().date()),
            "rows": int(len(merged))}
    meta_path(market, symbol).write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def covers(market: str, symbol: str, start: date, df: pd.DataFrame) -> bool:
    """這份快取當初有沒有問到 start 那麼早？"""
    req = read_meta(market, symbol).get("requested_start")
    if req:
        return req <= str(start)
    return df.index.min().date() <= start      # 舊版快取沒有 meta，退回舊判準


def usable(market: str, symbol: str, start: date, ttl_hours: float) -> pd.DataFrame | None:
    """快取夠新、且當初請求的區間涵蓋 start，才算可用。"""
    age = age_hours(market, symbol)
    if age is None or age > ttl_hours:
        return None
    df = load(market, symbol)
    if df is None or not covers(market, symbol, start, df):
        return None
    return df


def clear(market: str | None = None, symbol: str | None = None) -> int:
    """清快取，回傳刪除的檔案數。"""
    base = root() if market is None else root() / market.upper()
    if not base.exists():
        return 0
    files = [path(market, symbol)] if (market and symbol) else list(base.rglob("*.csv"))
    n = 0
    for f in files:
        if f.exists():
            f.unlink()
            n += 1
        m = f.with_suffix(".meta.json")
        if m.exists():
            m.unlink()
    return n
