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
- core/risk.py 單筆部位的風險尺度：停損距離 = N 個日標準差，夾在上下限
  之間。進場算停損價、出場判斷該不該認賠共用這一把尺（設定在
  config/rules.yaml 頂層 stop:）
- core/rules.py 出場規則 P0/P6/P7/P8/P10/P11/P13/V6-2/V6-2a
  （P0 = 硬性停損，虧損超過可承受範圍；severity 4，單獨觸發即全數出場。
  在它之前，七條規則全是技術面相對位置，沒有一條看得到成本價）
  ＋嚴重度加總分級（全數停損 / 降至 50% / 降至 75% / 續抱）
- core/recommend.py 進場評分（趨勢30/動能25/位階20/量能15/波動10）與排序。
  波動率是以 0.26 為峰值、對數距離的連續鐘形曲線（舊版區間內一律滿分，
  導致因子沒有鑑別度，復盤 IC 被離群值帶走）
- config/ markets.yaml（市場常數）＋ rules.yaml（規則門檻、評分權重）
- data/cache.py 本地 CSV 快取（預設 12 小時，記錄請求區間避免假性失效）
- data/universe.py ＋ config/universe/*.txt 三市場股票池
- cli.py：rec 推薦排序（可掃整池）/ check 持倉檢查 / doctor 環境診斷
  / cache 快取管理 / demo 煙霧測試
- web/ 網站版（FastAPI + Jinja2 + HTMX）：推薦排序（背景掃描＋進度輪詢）
  / 個股檢查 / 股票池編輯。web/logic.py、web/jobs.py 只組裝呼叫 core/
  與 data/，不重複計算
- web/auth.py 存取控制，fail closed：沒設 STOCKCORE_PASSWORD 就整個回 503。
  另有速率限制，擋在驗證之前。/healthz 是唯一免驗證端點
- 缺少市場相依套件時丟 data.base.MissingDependency，訊息說明本機與部署版
  各自怎麼修，不把原始 ModuleNotFoundError 丟給使用者
- Dockerfile ＋ docs/DEPLOY.md 可部署到任何吃 Docker 的 PaaS。
  python -m web 是本機模式（127.0.0.1 ＋ LOCAL_ONLY），不可用於對外部署
- core/backtest.py：復盤 ——「用 as_of 之前的資料算推薦，比對之後到 until
  的實際報酬」，檢驗 core/recommend.py 進場評分排序有沒有預測力（分數高
  是否後來真的表現較好），已扣市場成本。不是逐條規則回測（見下方待做）。
  cli.py review、網站版 /review 頁都接了這個模組。輸出含對照基準
  （整池等權不選股的報酬）與超額報酬、分位數分組（Q1~Q5）、逐因子 IC
  ＋離散度診斷（標準差／離散度／集中度，分辨真 IC 與被離群值帶出來的假 IC，
  門檻在 config/rules.yaml 的 score.ic_diagnostics）。
  web/summary.py 產生可一鍵複製的純文字摘要（表格標題一律 ASCII，
  中文全形字當標題貼到別處會對不齊）
- docs/CALIBRATION.md 驗證與校準方法：單一 as_of 不足以調權重、生存者
  偏誤（universe 是今天的清單）、台股未還原權息的偏差、樣本外紀律
- tests/ 179 passed，含 test_invariants.py 鐵則守門測試、test_web.py 網站層、
  test_auth.py 存取控制測試、test_tw_adapter.py、test_backtest.py

待做：
- core/rules.py 逐條規則獨立回測（P6/P7/... 各自的期望值，砍掉負的）——
  跟 core/backtest.py 現有的「進場評分復盤」不同，這個要逐日模擬規則
  觸發與停損執行，還沒做
- 基本面資料（P1–P5）、部位層風控、交易日曆

## 注意
回測必須扣交易成本（台股 58.5bps / A股 10bps / 美股 0bps），
A 股止損執行日要 +1 個交易日。
