"""美股 adapter —— yfinance（免費，無需 token）。

auto_adjust=True 即為還原權息後的價格。

美股三個結構性差異：
  1. 無漲跌停 —— 假跌破多，config 裡 p6_require_volume: true
  2. 零佣金   —— cost_bps: 0，短線訊號的成本門檻最低
  3. 盤前盤後 —— 本模型只吃正規盤日線，不處理 extended hours
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .base import MarketAdapter, MissingDependency


class USAdapter(MarketAdapter):
    market = "US"
    settlement = "T+0"
    adjusted = True

    def _fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        import logging

        try:
            import yfinance as yf
        except ImportError as e:
            raise MissingDependency(
                "美股資料需要 yfinance 套件，這個環境沒有安裝。\n"
                "本機：pip install yfinance\n"
                "部署版：確認 requirements-web.txt 有列 yfinance。"
            ) from e

        # yfinance 會把連線錯誤印成一大段雜訊蓋掉呼叫端的輸出，這裡壓掉；
        # 真正的失敗由上層看「回傳幾筆」來判斷。
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        df = yf.download(
            symbol.strip().upper(),
            start=str(start),
            end=str(end + timedelta(days=1)),   # yfinance end 是開區間
            auto_adjust=True,
            progress=False,
            actions=False,
        )
        if df is None or df.empty:
            return pd.DataFrame()

        # 單一標的仍可能回 MultiIndex 欄位，壓平
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        return df[["open", "high", "low", "close", "volume"]]
