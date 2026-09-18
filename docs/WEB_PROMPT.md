# 網站版開發 Prompt

貼進一個在 **stockcore repo 根目錄** 開啟的新 AI coding session。

---

## 開始複製

你要在這個既有的 Python 專案上加一層網站介面。**先讀 `CLAUDE.md` 和 `README.md`，
再讀 `cli.py`** —— `cli.py` 已經做完你要做的所有事，你的工作是把它從終端機搬到瀏覽器。

### 最重要的一條

**不准重寫任何計算或判斷邏輯。** 這個專案的鐵則是 `core/indicators.py` 是唯一
計算指標的地方。網站層是一層薄殼：接 HTTP 請求 → 呼叫既有函式 → 把回傳的 dict
轉成 JSON 或 HTML。

具體禁止事項：

- 不准在 JavaScript 裡算均線、乖離、回撤、評分，一個都不准
- 不准在 web 層寫 `if market == "TW"`，市場差異已經由 `config/markets.yaml` 驅動
- 不准把 `config/rules.yaml` 的門檻抄進前端或後端程式碼
- 不准修改 `core/` 底下任何檔案（要改先問我）
- 不准繞過 `data/cache.py` 自己抓資料

你唯一該新增的是 `web/` 目錄。`core/` 和 `data/` 只讀不改。

### 既有的 API（直接用，不要包裝過度）

```python
from data.base import get_adapter          # get_adapter("TW"|"US"|"CN")
from data import universe, cache
from core.indicators import compute, is_limit_down
from core.rules import evaluate
from core.recommend import score, rank

ad  = get_adapter("TW")
df  = ad.ohlcv("2330", "2025-01-01")       # 已快取、已正規化、已還原權息
m   = compute(df, symbol="2330", market="TW")

score(m)                      # 進場評分
evaluate(m, unrealized_pct=25, limit_down=False, adjusted=ad.adjusted)   # 出場判定
rank([m1, m2, ...], top=10, min_score=60)                               # 排序

universe.load("TW")           # [(代號, 名稱), ...]
universe.available()          # ["CN", "TW", "US"]
```

回傳的 dict 形狀（照抄成 API response，不要改欄位名）：

```python
score(m) -> {
  "symbol", "market", "as_of", "close", "score",        # score 是 0~100 float
  "rating",        # STRONG_BUY | BUY | NEUTRAL | REDUCE | AVOID
  "rating_label",  # 強烈推薦 | 推薦 | 中性觀望 | 偏空減碼 | 迴避
  "cost_penalty", "entry", "stop", "target", "risk_pct",
  "breakdown": [{"factor", "weight", "points", "detail"}, ...],   # 5 個因子
  "notes": [str, ...],
}

evaluate(m, ...) -> {
  "symbol", "market", "as_of", "cost_bps", "settlement", "unrealized_pct",
  "severity_score",
  "action",        # EXIT_ALL | REDUCE_50 | REDUCE_25 | HOLD | WATCH
  "action_label",  # 全數停損 | 降至 50% | 降至 75% | 續抱 | 跌停鎖死，隔日優先處理
  "triggered": ["P6", "P8", ...],
  "rules": [{"code", "name", "triggered", "severity", "detail"}, ...],   # 7 條全列
  "structural_notes": [str, ...],    # T+1、漲跌停、未還原權息、交易成本
}
```

### 技術選型

- 後端 **FastAPI + uvicorn**，放在 `web/app.py`
- 前端 **Jinja2 模板 + HTMX + Tailwind CDN**，放在 `web/templates/`、`web/static/`
- 不要 npm、不要 build step、不要 React。這是個人工具，裝一個 `pip install`
  就要能跑起來
- 新增依賴寫進 `requirements.txt`，標明是網站用的

啟動方式要能用一行指令：`python -m web`（在 `web/__main__.py` 實作）。

### 頁面

**1. 推薦排序 `/`**

市場下拉選單（TW / US / CN）＋「開始掃描」按鈕。掃完顯示一張表：
代號、名稱、分數、評級、現價、停損、目標、風險%。

- 分數欄用顏色分級（強烈推薦到迴避），**但顏色不能是唯一的資訊來源**，
  文字標籤要同時在
- 點一列展開該檔的五個因子得分條（trend/momentum/volume/position/volatility），
  每個因子顯示 `points/weight` 和 `detail` 文字
- 可以篩選 `min_score`、`top`
- 表頭必須顯示 `as_of` 日期和「成功 N/M 檔」

**2. 個股檢查 `/check`**

輸入市場、代號、未實現損益%。顯示：

- 上半：出場判定 —— `action_label` 大字、七條規則逐條列出（觸發的標紅，
  未觸發的也要列出來並顯示 detail，因為那是「還差多少」的資訊）
- 下半：進場評分 —— 分數、五因子拆解、停損/目標/風險%
- 最下：`structural_notes` 全部列出來，用警示樣式

**3. 股票池管理 `/universe`**

顯示 `config/universe/<市場>.txt` 的內容，可以在網頁上新增/刪除代號後寫回檔案。
寫檔前先備份成 `.txt.bak`。

### 一定會踩到的坑（先處理好）

1. **掃描很慢。** 40 檔冷快取要 30 秒以上（FinMind 每檔還 sleep 0.3 秒）。
   不要讓瀏覽器空轉等待。用背景工作 + 進度輪詢：
   `POST /api/scan` 回一個 job_id，`GET /api/scan/{job_id}` 回
   `{"status": "running"|"done"|"error", "done": 12, "total": 40, "result": [...]}`。
   前端用 HTMX 每秒 poll 一次，顯示「12/40」進度。

2. **阻塞式 I/O 會卡死 event loop。** `requests`、`yfinance`、`akshare` 都是同步的。
   一律用 `asyncio.to_thread()` 或 `run_in_executor` 丟到執行緒，
   不要直接在 `async def` 裡呼叫。

3. **不要併發打 API。** FinMind 免費層有速率限制，`data/tw.py` 裡的
   `time.sleep(0.3)` 是刻意留的。掃描要循序跑，不要開 thread pool 加速，
   會被封。

4. **單一標的失敗不能拖垮整批。** `cli.py` 的 `cmd_rec` 已經是這樣做的，照抄：
   收集 `failed` 清單，最後一起顯示。

5. **`FINMIND_TOKEN` 只能留在伺服器端。** 不准出現在任何模板、JS 或 API
   回應裡。

### 這是金融工具，以下是硬性要求

- **每一頁都要顯示 `as_of` 日期**，而且要顯眼。這是專案鐵則第 5 條。
  使用者看到一個分數卻不知道是哪天的資料，比沒有這個分數更危險。
- **`structural_notes` 不准折疊、不准省略。** 那裡面是「A 股 T+1 買了明天才能賣」、
  「跌停掛不出去」、「資料未還原權息所以結果不可信」這種會讓人賠錢的資訊。
- **每頁頁尾固定免責聲明**：本工具為技術指標計算結果，非投資建議；
  評分權重尚未經回測驗證；使用者自負盈虧。
- **`adapted=False` 時要跳明顯警告。** `adapter.adjusted` 為 False 代表拿到的是
  未還原權息的價格，所有價格類規則都不可信。

### 測試

- 現有 52 個測試必須全綠：`python -m pytest tests/ -q`
- 新增 `tests/test_web.py`，用 `fastapi.testclient.TestClient`
- **不准在測試裡打真實網路。** 用 `monkeypatch` 換掉 adapter，或預先塞
  `data/cache.py` 的快取檔（`cache.save(market, symbol, df, requested_start=...)`）。
  參考 `tests/test_cache.py` 的 `FakeAdapter` 寫法
- 至少要測到：掃描 job 的完整生命週期、單一標的失敗不影響整批、
  `as_of` 有出現在回應裡、股票池編輯有寫回檔案

### 完成後

更新 `README.md` 的用法段落和 `CLAUDE.md` 的「目前狀態」，
說明怎麼啟動網站版。commit 訊息寫清楚做了什麼。

先跟我確認一件事再動工：**這個網站要只跑在你自己的電腦（localhost），
還是要部署到公開網址？** 如果要公開，我們得先談驗證登入和 rate limiting，
那會改變不少設計。

## 複製到此結束
