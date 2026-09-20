"""A 股 adapter —— AkShare（免費，無需 token）。

adjust="qfq" 為前復權（還原權息）。

A 股三個結構性差異，在這裡標記，由 core/rules.py 處理：
  1. T+1     —— 當日買進不可賣，止損實際執行日要 +1 個交易日
  2. 漲跌停  —— 主板 ±10%、創業板/科創板 ±20%；跌停時掛不出去，止損失效
  3. ST 股   —— 退市風險，預設排除
"""

from __future__ import annotations

import re
from datetime import date

import pandas as pd

from .base import MarketAdapter, MissingDependency


def _akshare():
    """延遲匯入 akshare，並把原始的 ModuleNotFoundError 換成看得懂的說明。

    使用者在網頁上看到 "No module named 'akshare'" 完全不知道該做什麼；
    這個套件很重（約 299MB、拉進 32 個相依），所以部署時可能被刻意拿掉。
    """
    try:
        import akshare as ak
    except ImportError as e:
        raise MissingDependency(
            "A 股資料需要 akshare 套件，這個環境沒有安裝。\n"
            "本機：pip install akshare\n"
            "部署版：把 requirements-web.txt 裡的 akshare 取消註解後重新部署"
            "（會讓映像檔大上約 300MB）。"
        ) from e
    return ak


def board_of(symbol: str) -> str:
    """由代碼判斷板塊，決定漲跌停幅度。"""
    s = symbol.strip()[-6:]
    if s.startswith("688"):
        return "STAR"        # 科創板 ±20%
    if s.startswith("30"):
        return "CHINEXT"     # 創業板 ±20%
    if s.startswith("8") or s.startswith("4"):
        return "BSE"         # 北交所 ±30%
    return "MAIN"            # 主板 ±10%


LIMIT_PCT = {"MAIN": 0.10, "CHINEXT": 0.20, "STAR": 0.20, "BSE": 0.30}


class ChinaAdapter(MarketAdapter):
    market = "CN"
    settlement = "T+1"
    adjusted = True

    def __init__(self, exclude_st: bool = True):
        self.exclude_st = exclude_st

    def _fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        ak = _akshare()

        code = re.sub(r"\D", "", symbol)[-6:]

        if self.exclude_st and self._is_st(code):
            raise ValueError(f"[CN:{code}] 為 ST / *ST 股，已依設定排除")

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is None or df.empty:
            return pd.DataFrame()

        df = df.rename(
            columns={
                "日期": "dt",
                "开盘": "open",
                "最高": "high",
                "最低": "low",
                "收盘": "close",
                "成交量": "volume",
            }
        ).set_index("dt")
        return df[["open", "high", "low", "close", "volume"]]

    # ---------- A 股專屬 ----------
    def _is_st(self, code: str) -> bool:
        try:
            ak = _akshare()
            spot = ak.stock_zh_a_spot_em()
            row = spot[spot["代码"] == code]
            if row.empty:
                return False
            name = str(row.iloc[0]["名称"])
            return "ST" in name.upper()
        except Exception:
            return False  # 查不到就放行，但上層應標記為未驗證

    def limit_pct(self, symbol: str) -> float:
        return LIMIT_PCT[board_of(symbol)]

    def chips(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        """融資融券餘額。對應你框架 S2 的「融資逆勢連 3 增」。"""
        ak = _akshare()

        code = re.sub(r"\D", "", symbol)[-6:]
        market = "sh" if code.startswith("6") else "sz"
        try:
            df = ak.stock_margin_detail_sse() if market == "sh" else pd.DataFrame()
        except Exception:
            return pd.DataFrame()
        return df
