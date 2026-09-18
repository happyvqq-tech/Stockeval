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
已完成：
- data/base.py 統一 OHLCV 格式與 normalize（排序、去重、剔除停牌日）
- 三市場 adapter：TW FinMind、US yfinance、CN AkShare
- core/indicators.py 指標
- core/rules.py 出場規則 P6/P7/P8/P10/P11/P13/V6-2/V6-2a
  ＋嚴重度加總分級（全數停損 / 降至 50% / 降至 75% / 續抱）
- core/recommend.py 進場評分（趨勢30/動能25/位階20/量能15/波動10）與排序
- config/ markets.yaml（市場常數）＋ rules.yaml（規則門檻、評分權重）
- cli.py：rec 推薦排序 / check 持倉檢查 / demo 煙霧測試
- tests/ 30 passed，含 test_invariants.py 鐵則守門測試

待做：backtest.py、基本面資料（P1–P5）、部位層風控、資料快取層

## 注意
回測必須扣交易成本（台股 58.5bps / A股 10bps / 美股 0bps），
A 股止損執行日要 +1 個交易日。
