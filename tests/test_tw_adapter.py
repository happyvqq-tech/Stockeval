"""台股 adapter 測試 —— 不打真實 FinMind，用假回應驗證行為。

這裡釘住一個實際在 Render 部署上發生過的 bug：免費 FinMind token 對
TaiwanStockPriceAdj（還原股價）沒有存取權限時，FinMind 回 HTTP 400，
而不是空的 JSON payload。修正前 requests.raise_for_status() 會直接把
這個 400 炸出去，永遠走不到「退回未還原股價」那條路（見 data/tw.py
檔頭說明），使用者在個股檢查頁就會看到原始的 400 錯誤訊息。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest
import requests

from data.tw import TaiwanAdapter


def _resp(status_code: int, payload: dict | None = None):
    r = requests.Response()
    r.status_code = status_code
    r._content = json.dumps(payload or {}).encode("utf-8")
    return r


def _plain_price_rows(n=65):
    rows = []
    for i in range(n):
        d = f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"
        rows.append({"date": d, "open": 100.0, "max": 101.0, "min": 99.0,
                     "close": 100.0, "Trading_Volume": 10_000})
    return rows


@pytest.mark.parametrize("status", [400, 401, 403, 500, 502])
def test_adj_dataset_http_error_falls_back_to_plain_price(status):
    """核心回歸測試：Adj 資料集回任何 HTTP 錯誤都要退回 TaiwanStockPrice，
    不能整個炸掉。

    400/401/403 是免費 token 沒有存取權（FinMind 用這幾種狀態碼表達，
    行為不保證只有 400），500/502 是伺服器端問題 —— 兩種情況都一樣：
    我們無法確認拿到的是還原股價，所以退回未還原資料集並誠實標記
    adjusted=False，而不是讓整個請求失敗。"""
    ad = TaiwanAdapter(token="")
    responses = [_resp(status), _resp(200, {"data": _plain_price_rows()})]

    with patch("data.tw.requests.get", side_effect=responses) as mock_get, \
         patch("data.tw.time.sleep"):
        df = ad.ohlcv("6919", "2025-01-01", "2025-03-31")

    assert mock_get.call_count == 2
    assert len(df) == 65
    assert ad.adjusted is False          # 一定要標記成未還原，否則違反鐵則 4


def test_adj_dataset_success_does_not_call_plain_price():
    """Adj 資料集正常回傳時（贊助會員 token），不該多打一次 API。"""
    ad = TaiwanAdapter(token="sponsor-token")
    responses = [_resp(200, {"data": _plain_price_rows()})]

    with patch("data.tw.requests.get", side_effect=responses) as mock_get, \
         patch("data.tw.time.sleep"):
        df = ad.ohlcv("2330", "2025-01-01", "2025-03-31")

    assert mock_get.call_count == 1
    assert len(df) == 65
    assert ad.adjusted is True


def test_empty_adj_payload_still_falls_back():
    """既有行為不能因為這次修正而壞掉：空 payload（200 但無資料）一樣要退回。"""
    ad = TaiwanAdapter(token="")
    responses = [_resp(200, {"data": []}), _resp(200, {"data": _plain_price_rows()})]

    with patch("data.tw.requests.get", side_effect=responses), \
         patch("data.tw.time.sleep"):
        df = ad.ohlcv("2330", "2025-01-01", "2025-03-31")

    assert len(df) == 65
    assert ad.adjusted is False


def test_both_datasets_failing_raises_from_second_call():
    """兩個資料集都拿不到資料時，例外要從第二次呼叫傳出來，不能被第一次的
    400 fallback 邏輯誤吞。"""
    ad = TaiwanAdapter(token="")
    responses = [_resp(400), _resp(500)]
    with patch("data.tw.requests.get", side_effect=responses), \
         patch("data.tw.time.sleep"):
        with pytest.raises(requests.HTTPError):
            ad.ohlcv("2330", "2025-01-01", "2025-03-31")
