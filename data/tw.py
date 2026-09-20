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

from .base import MarketAdapter, QuotaExceeded

API = "https://api.finmindtrade.com/api/v4/data"


class TaiwanAdapter(MarketAdapter):
    market = "TW"
    settlement = "T+0"

    # FinMind 用 402 表達「額度用完／需要付費」。這跟 400/401/403（單純
    # 沒有這個資料集的權限）要分開處理：前者整個帳號都沒得用了，後者只是
    # 該換一個資料集。
    QUOTA_STATUS = 402

    def __init__(self, token: str | None = None, sleep: float = 0.3):
        self.token = token or os.getenv("FINMIND_TOKEN", "")
        self.sleep = sleep
        self.adjusted = True
        # 一旦確認這個 token 拿不到還原股價，就別再試了。不記住的話每一檔
        # 都會先打一次注定失敗的 Adj，請求數直接翻倍，免費額度很快就燒完。
        self._adj_available = True

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

    def _status_of(self, err: requests.HTTPError) -> int | None:
        return err.response.status_code if err.response is not None else None

    def _fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raw = pd.DataFrame()

        if self._adj_available:
            try:
                raw = self._get("TaiwanStockPriceAdj", symbol, start, end)
            except requests.HTTPError as e:
                if self._status_of(e) == self.QUOTA_STATUS:
                    raise QuotaExceeded(
                        "FinMind 額度已用完（402）。免費層額度有限，"
                        "請稍後再試、減少一次查詢的檔數，或設定贊助會員 "
                        "FINMIND_TOKEN。"
                    ) from e
                # 免費 token 對 TaiwanStockPriceAdj 沒有存取權限時，FinMind 回
                # 4xx 而不是空結果，要在這裡接住才走得到下面的退回邏輯。
                # 記住這個結果，後面的標的就不用再白打一次。
                self._adj_available = False
                self.adjusted = False

        if raw.empty:
            try:
                raw = self._get("TaiwanStockPrice", symbol, start, end)
            except requests.HTTPError as e:
                if self._status_of(e) == self.QUOTA_STATUS:
                    raise QuotaExceeded(
                        "FinMind 額度已用完（402）。免費層額度有限，"
                        "請稍後再試、減少一次查詢的檔數，或設定贊助會員 "
                        "FINMIND_TOKEN。"
                    ) from e
                raise
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
