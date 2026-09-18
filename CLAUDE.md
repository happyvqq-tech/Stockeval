# stockcore

## 鐵則
1. 市場專屬程式碼只能出現在 data/。core/ 以下絕對不准
   出現 if market == "TW" 這類數值分支。
2. 數值門檻一律從 config/markets.yaml 讀，程式裡不寫死。
3. 只有結構性差異（T+1、漲跌停）才允許 if market。
4. 所有價格必須是還原權息。新增 adapter 時先確認這點。
5. 每個輸出都要帶 as_of 日期。

## 架構
data/base.py  定義統一 OHLCV 格式，其餘 adapter 都要繼承 MarketAdapter
core/indicators.py  唯一計算指標的地方
core/rules.py  純布林判斷，不呼叫 LLM

## 測試
python run_demo.py  —— 不需網路，改動 core/ 後必跑

## 目前狀態
已完成：三市場 adapter、指標、P6/P7/P8/P10/P11/P13/V6-2
待做：backtest.py、基本面資料、部位層風控

## 注意
回測必須扣交易成本（台股 58.5bps / A股 10bps / 美股 0bps），
A 股止損執行日要 +1 個交易日。
