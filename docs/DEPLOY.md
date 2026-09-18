# 部署到公開網址

目標：打開網址就能用，不必先在自己電腦上跑指令。

## 先讀這段

放上公開網址後，**任何拿到網址的人都連得到**。所以這個專案的存取控制
設計成 fail closed：沒設 `STOCKCORE_PASSWORD` 的話，每個請求都回 503，
服務等於不存在。這是刻意的，別為了方便把它拿掉。

要保護的東西有三樣：

- **你的 FinMind token 額度** —— 免費層有上限，被人代跑就沒了
- **伺服器資源** —— 掃一次整池要跑 30 秒以上
- **你的持倉資訊** —— 個股檢查頁會顯示你輸入的未實現損益

## 選哪個平台

**建議 Render。** 從 GitHub repo 部署最直接，偵測到 `Dockerfile` 就自動建置，
免費方案不必綁信用卡，自動給 HTTPS（HTTP Basic 密碼是明文傳輸，沒有 HTTPS
等於沒有保護），區域可選新加坡，是離台灣最近的免費區域。
repo 裡的 `render.yaml` 可以讓你用 Blueprint 一次帶入設定。

**次選 Zeabur。** 台灣團隊做的，介面和文件都是繁體中文，出問題找資料比較快，
一樣支援 Dockerfile。免費額度比 Render 緊一些。

**不建議當第一選擇**：Railway 好用但已經不是真的免費（試用額度用完要付費）；
Fly.io 要綁卡而且要裝 CLI、寫 `fly.toml`，對這個需求太複雜；
Google Cloud Run 免費額度其實很夠、台灣還有 asia-east1 區域，但要開 GCP 專案
並啟用帳單，設定步驟多很多。

> 各家免費方案的條款變動頻繁，實際額度、是否綁卡、休眠規則請以你開帳號當下
> 的官方說明為準。上面的建議理由（Docker 原生、不綁卡、自動 HTTPS、區域近）
> 才是挑選的重點。

## 需要準備

1. 一個吃 Dockerfile 的 PaaS 帳號（`Dockerfile` 不綁任何平台，換家也能用）
2. 這個 repo 推上 GitHub
3. 一組夠長的密碼（至少 8 字元，太短會被當成沒設）

## 步驟

### 1. 建立服務

在平台上選「從 GitHub repo 部署」，指定這個 repo 與分支。平台偵測到
`Dockerfile` 後會自動用它建置，不需要額外指定 build command 或 start command。

Render 的話兩種方式都行：

- **New > Blueprint**，指到這個 repo，會讀 `render.yaml` 帶入方案、區域、
  健康檢查路徑，你只要填兩個密碼欄位
- **New > Web Service**，手動選 Docker、Free 方案、Singapore 區域

容器裝的是 `requirements-web.txt`（精簡版），不含 `pytest` 與 `akshare`，
建置比較快、映像檔比較小。**要在部署版用 A 股**，把 `requirements-web.txt`
裡 `akshare` 那行取消註解再重新部署。

容器的啟動指令已經寫在 `Dockerfile` 裡：

```
uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'
```

`${PORT}` 會自動吃平台注入的埠號，不用改。

### 2. 設定環境變數

在平台的 Environment / Secrets 頁面設定：

| 變數 | 必要性 | 說明 |
|------|--------|------|
| `STOCKCORE_PASSWORD` | **必填** | 至少 8 字元。沒設服務就不會回應 |
| `FINMIND_TOKEN` | 台股需要 | 沒有的話台股會退回未還原權息的股價 |
| `STOCKCORE_RATE_LIMIT` | 選填 | 每分鐘請求上限，預設 60 |
| `STOCKCORE_CACHE` | 選填 | 快取路徑，預設 `/app/cache` |

**這兩個值只能放在平台的環境變數裡，絕對不要寫進程式碼或 commit 進 repo。**

### 3. 健康檢查

若平台要你指定健康檢查路徑，填 `/healthz`。這個端點刻意不受密碼保護
（否則平台探不到會判定部署失敗），而它只回 `{"status": "ok"}`，不吐任何資料。

### 4. 開啟網址

瀏覽器會跳出帳號密碼對話框。**帳號欄位填什麼都可以**，只驗證密碼。

## 部署後會遇到的狀況

**第一次開很慢。** 免費方案的服務閒置一段時間會休眠，下次請求要等容器重新
啟動，可能 30～60 秒。這是免費方案的特性，不是程式的問題。

**快取會消失。** 容器的檔案系統是暫時的，重啟或重新部署後 `cache/` 就空了，
下一次掃描要重新抓全部資料，比較慢也比較容易撞到 FinMind 速率限制。
想讓快取存活就在平台掛一顆 volume，然後把 `STOCKCORE_CACHE` 指到掛載路徑。

**股票池的修改也會消失。** `/universe` 頁面是寫進容器裡的
`config/universe/*.txt`，同樣會被重新部署洗掉。要永久保存，請直接改
repo 裡的檔案後重新部署，或把 volume 掛到 `config/universe`。

**HTTP Basic 的密碼是明文傳輸**，只有在 HTTPS 底下才安全。主流 PaaS 預設
就給 HTTPS；如果你自己架反向代理，請自行確認憑證有裝好，不要用純 HTTP 對外。

## 本機還是可以跑

```bash
python -m web
```

這個進入點會自動帶 `STOCKCORE_LOCAL_ONLY=1` 並只綁 `127.0.0.1`，
所以在自己電腦上不需要設密碼。**不要把這個進入點用在對外部署** ——
它沒有走 fail-closed 那條路徑。

## 想收回服務

把平台上的服務刪掉或暫停即可。也可以只把 `STOCKCORE_PASSWORD` 清空，
服務會立刻對所有請求回 503。
