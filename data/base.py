"""市場 adapter 基底 + 統一格式正規化。

外面看到的只有三件事：
    get_adapter("TW").ohlcv(symbol, start, end) -> 統一格式 DataFrame
    adapter.chips(...)                          -> 籌碼面（沒有就回空表）
    adapter.limit_pct(symbol)                   -> 漲跌停幅度（沒有就 None）

統一格式的定義（core/ 只認這個）：
    index  = DatetimeIndex，遞增、無重複、無時區
    欄位   = open / high / low / close（float，已還原權息）、volume（int）
    已剔除 volume == 0 的停牌日與任何含 NaN 的列
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime

import pandas as pd

COLUMNS = ["open", "high", "low", "close", "volume"]


def _as_date(x) -> date:
    if x is None:
        return date.today()
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return pd.Timestamp(x).date()


def normalize(df: pd.DataFrame, *, market: str = "", symbol: str = "") -> pd.DataFrame:
    """把任何 adapter 的原始輸出壓成統一格式。所有清洗只發生在這裡。"""
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=COLUMNS)

    out = df.copy()

    missing = [c for c in COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"[{market}:{symbol}] adapter 缺少欄位 {missing}")

    out.index = pd.to_datetime(out.index, errors="coerce")
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    out.index.name = "dt"

    for c in COLUMNS:
        out[c] = pd.to_numeric(out[c], errors="coerce")

    out = out[COLUMNS]
    out = out[~out.index.isna()]
    out = out.dropna()
    out = out[out["volume"] > 0]          # volume == 0 視為停牌，直接剔除
    out = out[~out.index.duplicated(keep="last")].sort_index()
    out["volume"] = out["volume"].astype("int64")
    return out


class MarketAdapter(ABC):
    """所有市場專屬程式碼都只能待在子類別裡。"""

    market: str = ""
    settlement: str = "T+0"
    adjusted: bool = True          # 是否已還原權息；False 時上層必須警告

    def ohlcv(self, symbol: str, start, end=None) -> pd.DataFrame:
        raw = self._fetch(symbol, _as_date(start), _as_date(end))
        return normalize(raw, market=self.market, symbol=symbol)

    @abstractmethod
    def _fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """回傳原始 DataFrame，欄位需已改名為 COLUMNS，index 為日期。"""

    # ---------- 選配，沒有資料來源就維持預設 ----------
    def chips(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame()

    def limit_pct(self, symbol: str) -> float | None:
        return None


_REGISTRY = {}


def get_adapter(market: str, **kwargs) -> MarketAdapter:
    """延遲匯入：只有真的要用某個市場，才需要裝那個市場的套件。"""
    key = (market or "").upper()

    if key in _REGISTRY and not kwargs:
        return _REGISTRY[key]

    if key == "TW":
        from .tw import TaiwanAdapter as cls
    elif key == "US":
        from .us import USAdapter as cls
    elif key == "CN":
        from .cn import ChinaAdapter as cls
    else:
        raise KeyError(f"未知市場 {market!r}，目前支援 TW / US / CN")

    adapter = cls(**kwargs)
    if not kwargs:
        _REGISTRY[key] = adapter
    return adapter
