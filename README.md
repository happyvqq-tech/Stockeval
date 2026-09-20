# stockcore

台股 / 美股 / A 股三市場共用的量化規則骨架 —— 進場推薦 + 持倉出場判定。

## 分層原則

```
data/    市場專屬程式碼「只」存在於這一層
core/    完全不知道自己在處理哪個市場（沒有任何 if market == "TW"）
config/  所有數值門檻，改參數不動程式
```

專案鐵則見 `CLAUDE.md`，由 `tests/test_invariants.py` 自動把關。

市場差異一律走 `config/markets.yaml`。新增一個市場＝加一段 YAML ＋寫一個 adapter，
`core/` 不用動一行。

## 安裝

```bash
pip install -r requirements.txt
export FINMIND_TOKEN=你的token        # 只有台股需要；沒有的話會退回未還原股價
```

## 快速上手

```bash
python cli.py doctor        # 先跑這個：檢查套件、金鑰、快取、股票池、連線
python cli.py rec --market TW          # 掃整個股票池，依分數排序
python cli.py check --market TW --symbol 2330 -u 25   # 我抱著且賺 25%，該賣嗎
```

`doctor` 會逐項告訴你缺什麼、該裝什麼，不用自己猜。

`rec` 不給 `--symbols` 就掃 `config/universe/<市場>.txt` 的股票池：

```
TW 推薦排序　as_of 2026-09-18　成功 6/6　快取命中 6
代號     名稱            分數  評級            現價      停損      目標
2454     聯發科          82.3  強烈推薦      156.03    143.55    180.99
2317     鴻海            79.3  強烈推薦      133.09    122.44    154.39
2330     台積電          58.8  中性觀望      267.57    246.16    310.39
```

加 `-v` 看每個因子的得分與理由，加 `--top 10`、`--min-score 60` 篩選。

**股票池**在 `config/universe/TW.txt`（每行「代號 名稱」，`#` 為註解），
附的是大型股參考清單，不是即時成分股，請自行增刪維護。

**快取**存在 `./cache/<市場>/<代號>.csv`，預設 12 小時內不重抓 ——
FinMind 免費層有速率限制，掃幾十檔沒有快取一定被擋。

```bash
python cli.py cache              # 看目前快取了什麼
python cli.py cache --clear      # 清掉
python cli.py rec --market TW --no-cache    # 這次強制重抓
```
環境變數：`STOCKCORE_CACHE` 改路徑、`STOCKCORE_NO_CACHE=1` 整個停用。

不用網路的煙霧測試：`python run_demo.py`

## 復盤（評分排序的事後檢驗）

```bash
python cli.py review --market US --asof 2026-08-01
python cli.py review --market TW --asof 2026-08-01 --until 2026-09-01 --top 10
```

拿一個過去的日期，算出「只用當時看得到的資料」會產生的推薦排序，再看到
現在（或另一個日期）為止的實際報酬，藉此檢查 `core/recommend.py` 的評分
排序有沒有預測力——分數高的標的，後來真的表現比較好嗎？網站版在 `/review`。

輸出的重點有三塊：

1. **對照基準** —— 整池等權、完全不選股的報酬，以及選股組合的超額報酬。
   沒有這個對照，「平均賺 8%」毫無意義：大盤同期漲 10% 的話，評分是負貢獻。
2. **逐因子 IC ＋ 離散度診斷** —— 五個因子各自的 IC，以及判斷這個 IC
   能不能當真所需的標準差／離散度／集中度。一個因子如果對多數標的給出
   相同分數，IC 會被少數離群值帶走，會被標記為「鑑別度不足」。要調
   `config/rules.yaml` 的權重，看這個，不要看總分。
3. **分位數分組（Q1~Q5）** —— 各組的平均報酬與超額報酬。報酬由 Q1 到 Q5
   單調遞減，才代表排序真的有效。
4. 平均淨報酬（已扣市場成本）、勝率、分數前半／後半的報酬對照。

**單一 as_of 日期的結果不足以調權重** —— 同一市場同一期間的標的高度相關，
有效樣本數接近 1 而不是 40。完整的驗證與校準方法（含生存者偏誤、樣本外
紀律、各陷阱）見 **[docs/CALIBRATION.md](docs/CALIBRATION.md)**。

**這不是逐條規則的出場回測**：不模擬持有期間 P6/P8 等規則觸發停損，是
單純的「買進持有到某天」比較。逐條規則的期望值回測仍在待辦清單。

**as_of 的正確性有測試把關**：`tests/test_backtest.py` 直接證明
`snapshot()` 從不向 adapter 要求超過 `asof` 的資料 —— 這是復盤功能唯一
不能妥協的前提，偷看到未來資料的回測毫無意義。

## 網站版

```bash
python -m web
```

打開 <http://127.0.0.1:8000>。**只綁本機（127.0.0.1），不對外開放**，
自用不需要處理登入或防護。

三個頁面：

- `/`（推薦排序）：選市場、可留空掃整個股票池，掃描跑在背景、進度即時輪詢，
  點一列展開五個因子的得分細節
- `/check`（個股檢查）：輸入代號和未實現損益%，同時看出場判定（七條規則
  逐條列出，包含未觸發的，因為那是「還差多少」的資訊）與進場評分
- `/universe`（股票池管理）：直接在網頁上增刪 `config/universe/*.txt`
  的代號，每次修改前自動備份成 `.bak`
- `/review`（復盤）：網站版的「事後檢驗」，同上，會跑得比推薦排序慢
  （每檔要抓兩段時間的資料），且刻意不用快取（見下方限制）

網站層（`web/`）只呼叫 `core/` 和 `data/` 既有的函式，不重複計算任何指標，
`web/logic.py` 是所有路由共用的組裝邏輯。換 port：`STOCKCORE_WEB_PORT=8080 python -m web`。

### 部署到公開網址

`Dockerfile` 可直接丟給任何吃 Docker 的 PaaS（建議 Render，`render.yaml` 已備好）。
完整步驟見 **[docs/DEPLOY.md](docs/DEPLOY.md)**。

容器裝的是 `requirements-web.txt`，不含 `pytest` 與 `akshare`（A 股要用的話
把該行取消註解）。

存取控制是 fail closed：**沒設 `STOCKCORE_PASSWORD`（至少 8 字元）就整個服務
回 503**，不會有「預設開放」的狀態。設了之後用 HTTP Basic 驗證，帳號不檢查、
只比對密碼（用 `compare_digest` 避免計時攻擊）。另有每分鐘請求上限
（`STOCKCORE_RATE_LIMIT`，預設 60），擋在驗證之前，所以狂送錯密碼也耗不掉資源。

`/healthz` 是唯一免驗證的端點，供平台健康檢查用，只回 `{"status": "ok"}`。

`python -m web` 是本機模式（綁 127.0.0.1、自動帶 `STOCKCORE_LOCAL_ONLY=1`），
不要拿來對外部署。

## 程式介面

```python
from data.base import get_adapter
from core.indicators import compute
from core.rules import evaluate
from core.recommend import score, rank

df  = get_adapter("TW").ohlcv("2330", "2025-01-01")
m   = compute(df, symbol="2330", market="TW")

evaluate(m, unrealized_pct=25)   # 有部位 → 該不該出場
score(m)                         # 沒部位 → 該不該進場
rank([m1, m2, m3], top=5)        # 一籃子標的排序
```

`evaluate()` / `score()` 的輸出都是 JSON-safe 的扁平 dict，**那份才送給 LLM**。
LLM 不做任何計算、不做任何判斷，只把數字寫成人話。

## 規則表

| 代號 | 名稱 | 嚴重度 | 說明 |
|------|------|--------|------|
| P6 | 黑K跌破20MA | 2 | 帶 20MA 斜率盤整濾網；`p6_require_volume` 市場另需量能確認 |
| P7 | 爆量下跌 | 1 | 量比 ≥ 2.0 且收黑 |
| P8 | 連續收在20MA下 | 2 | 預設 3 日 |
| P10 | 移動止盈 | 1 | 未實現 ≥ 15% 才啟動，跌破 10MA 觸發 |
| P11 | 向下跳空 | 1 | 開盤跳空 ≤ -3% |
| P13 | 乖離率過大 | 1 | 正乖離 ≥ 15%，過熱減碼而非停損 |
| V6-2 | 自60日高回撤 | 2 | ≤ -20% |
| V6-2a | 高波動股回撤 | 2 | 年化波動 ≥ 0.45 時改用 -28% 門檻 |

嚴重度加總 → 動作：`≥4 全數停損`／`≥2 降至 50%`／`≥1 降至 75%`／`0 續抱`。

進場評分（滿分 100）：趨勢 30 ＋ 動能 25 ＋ 位階 20 ＋ 量能 15 ＋ 波動 10，
再依市場成本扣分（台股 58.5bps 扣 2.9 分，美股 0bps 不扣）。

## 已驗證行為

`pytest tests/ -q` → 143 passed。以下每一條都有對應測試：

- 美股 `p6_require_volume: true` → 量比不足時不觸發 P6（假跌破過濾）
  → `test_p6_filtered_in_us_by_volume`
- 同一組資料（P6 為邊際規則）：台股/A 股「全數停損」，美股「降至 50%」
  → `test_same_data_different_action_across_markets`
- A 股自動帶出 T+1 結構性提示，台股不帶 → `test_cn_emits_t1_note`
- 成本：台股 58.5bps、A 股 10bps、美股 0bps → `test_cost_bps_per_market`
- 20MA 盤整時黑K破位不算破位 → `test_flat_ma20_suppresses_p6`
- 未還原權息、跌停鎖死 → 均在 `structural_notes` 明確標記
- `normalize()` 排序、去重、剔除停牌日（volume == 0）

`tests/test_invariants.py` 是 CLAUDE.md 五條鐵則的守門測試：用 AST 掃描
`core/` 有無市場數值分支（鐵則 1），並實際改掉 config 門檻確認行為跟著變
（鐵則 2）、確認每個輸出都帶 as_of（鐵則 5）。改動 `core/` 後這份必須綠。

`run_demo.py`（seed=7，合成資料）的回撤為 **-21.35%**，觸發 P8 / P10 / V6-2，
三市場同為「全數停損」—— 這組資料裡 P6 未觸發，所以看不出市場差異，
市場差異請看上面那條測試。

## 下一步（建議順序）

1. `core/backtest.py` —— 逐條規則獨立回測，砍掉期望值為負的
2. `data/fundamentals/` —— 台股月營收、美股 EDGAR、A 股財報（P1–P5）
3. `core/portfolio.py` —— 相關性與產業集中度（目前完全沒有部位層風控）
4. `llm/report.py` —— 只吃 evaluate() / score() 的 JSON，產出報告

## 已知缺口

- `data/cn.py` 的 `chips()` 只是佔位，融資融券還沒接完
- 美股籌碼面（short interest / Form 4）另建模組，不適合放日線流程
- 尚未接交易日曆（`exchange_calendars`），跨市場對齊會有誤差
- 部署後容器檔案系統是暫時的：快取與 `/universe` 頁面的股票池修改會在
  重新部署後消失，要保存得掛 volume
- HTTP Basic 密碼是明文傳輸，只有在 HTTPS 底下才安全
- 復盤（/review、cli.py review）刻意不用本地快取：`data/cache.py` 的
  有效性判斷只看 requested_start、不看 end，如果復盤把「只到某天為止」
  的資料存進共用快取，之後正常的推薦排序查詢會被誤判成快取夠用，拿到
  被截斷的舊資料。所以復盤每次都重新抓取，會比推薦排序慢也更容易撞到
  FinMind 速率限制，一次不要對太多檔跑復盤
- 進場評分的權重是先驗設定，**尚未經過回測驗證**，別直接拿去下單
- 股票池是靜態檔案，不會自動跟著成分股調整而更新
- 快取以「請求區間 + 12 小時」判定新舊，沒有接交易日曆，
  遇到連假可能拿到前一個交易日的資料（`as_of` 會誠實反映）
