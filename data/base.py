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

from . import cache

COLUMNS = ["open", "high", "low", "close", "volume"]


class MissingDependency(RuntimeError):
    """這個市場的資料來源需要的套件沒有安裝。

    跟一般錯誤分開，是因為使用者看到原始的 ModuleNotFoundError
    （例如 "No module named 'akshare'"）完全不知道該做什麼。
    """


class QuotaExceeded(RuntimeError):
    """資料來源的額度／配額用完了（例如 FinMind 免費層回 402）。

    跟「這一檔抓不到」不同：額度是帳號層級的，剩下的標的必定同樣失敗。
    批次處理的迴圈看到這個例外要直接中止整批，不要逐檔重試 —— 繼續打
    只會更慢，而且把額度燒得更乾淨。
    """


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
    from_cache: bool = False       # 上一次 ohlcv() 是否走快取

    def ohlcv(self, symbol: str, start, end=None, *, use_cache: bool = True,
              ttl_hours: float = cache.DEFAULT_TTL_HOURS) -> pd.DataFrame:
        s, e = _as_date(start), _as_date(end)

        if use_cache and cache.enabled():
            hit = cache.usable(self.market, symbol, s, ttl_hours)
            if hit is not None:
                self.from_cache = True
                return hit.loc[str(s):str(e)]

        self.from_cache = False
        df = normalize(self._fetch(symbol, s, e), market=self.market, symbol=symbol)
        # 裁到 [s, e]：正常情況下 adapter 會照 start/end 跟資料源要資料，這行
        # 不會改變任何結果；但一旦哪個 adapter 多給了範圍外的資料，這裡是唯一
        # 擋住的地方 —— compute() 無條件把最後一列當成 as_of（鐵則 5），
        # 一列不小心夾帶的未來資料就會讓「as_of」名不符實，對回測尤其致命。
        # 快取命中那條路徑本來就有做這件事（見上面 hit.loc[...]），這裡補齊
        # 讓兩條路徑保證一致。
        df = df.loc[str(s):str(e)]
        if use_cache and cache.enabled():
            cache.save(self.market, symbol, df, requested_start=s)
        return df

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
