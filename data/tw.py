"""台股 adapter —— FinMind。

還原權息：使用 TaiwanStockPriceAdj 資料集。
  這個 dataset 需要 FinMind 贊助會員 token。若只有免費 token，
  會退回 TaiwanStockPrice（未還原），並發出警告 —— 此時你框架裡
  「除權息豁免鐵則」才需要存在；用還原股價的話那條規則可以整條刪掉。

環境變數：FINMIND_TOKEN
"""

from __future__ import annotations

import os
import time
from datetime import date

import pandas as pd
import requests

from .base import MarketAdapter

API = "https://api.finmindtrade.com/api/v4/data"


class TaiwanAdapter(MarketAdapter):
    market = "TW"
    settlement = "T+0"

    def __init__(self, token: str | None = None, sleep: float = 0.3):
        self.token = token or os.getenv("FINMIND_TOKEN", "")
        self.sleep = sleep
        self.adjusted = True

    def _get(self, dataset: str, symbol: str, start: date, end: date) -> pd.DataFrame:
        params = {
            "dataset": dataset,
            "data_id": symbol,
            "start_date": str(start),
            "end_date": str(end),
        }
        if self.token:
            params["token"] = self.token
        r = requests.get(API, params=params, timeout=30)
        time.sleep(self.sleep)  # 免費層有速率限制，別拿掉
        r.raise_for_status()
        payload = r.json()
        return pd.DataFrame(payload.get("data", []))

    def _fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raw = self._get("TaiwanStockPriceAdj", symbol, start, end)

        if raw.empty:
            raw = self._get("TaiwanStockPrice", symbol, start, end)
            self.adjusted = False

        if raw.empty:
            return raw

        df = raw.rename(
            columns={
                "date": "dt",
                "open": "open",
                "max": "high",
                "min": "low",
                "close": "close",
                "Trading_Volume": "volume",
            }
        ).set_index("dt")
        return df[["open", "high", "low", "close", "volume"]]

    def chips(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """三大法人買賣超（張）。用於 S2 籌碼面判定。"""
        raw = self._get("TaiwanStockInstitutionalInvestorsBuySell", symbol, start, end)
        if raw.empty:
            return pd.DataFrame()
        raw["net"] = (raw["buy"] - raw["sell"]) / 1000
        wide = raw.pivot_table(index="date", columns="name", values="net", aggfunc="sum")
        wide.index = pd.to_datetime(wide.index)
        # 統一欄名：Foreign_Investor → foreign, Investment_Trust → trust
        return wide.rename(
            columns={
                "Foreign_Investor": "foreign",
                "Investment_Trust": "trust",
                "Dealer_self": "dealer",
            }
        )
