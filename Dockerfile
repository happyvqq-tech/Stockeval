# stockcore 網站版容器。可部署到任何吃 Dockerfile 的平台
# （Render / Railway / Fly.io / Zeabur / Koyeb 等）。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先裝相依再複製程式碼，改程式時不必重裝套件。
# 用精簡版清單：不裝 pytest 與 akshare，免費方案的建置快很多。
# 要在部署版用 A 股，去 requirements-web.txt 把 akshare 那行取消註解。
COPY requirements-web.txt .
RUN pip install --no-cache-dir -r requirements-web.txt

COPY . .

# 預設快取放在容器內（重啟就消失）。想讓快取跨重啟存活，
# 在平台掛一顆 volume 然後把 STOCKCORE_CACHE 指過去。
ENV STOCKCORE_CACHE=/app/cache
RUN mkdir -p /app/cache

EXPOSE 8000

# 注意：這裡沒有 STOCKCORE_LOCAL_ONLY，所以沒設 STOCKCORE_PASSWORD
# 的話每個請求都會回 503 —— 這是刻意的，不要拿掉。
# --proxy-headers 讓 uvicorn 讀平台反向代理送來的真實來源資訊。
CMD ["sh", "-c", "uvicorn web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
