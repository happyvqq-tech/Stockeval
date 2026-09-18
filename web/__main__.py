"""啟動開發伺服器：python -m web

這個進入點是「本機模式」：綁 127.0.0.1，並自動帶上 STOCKCORE_LOCAL_ONLY=1
讓存取控制放行，所以自己電腦上跑不需要設密碼。

部署到公開網址時**不要用這個進入點**，改用 Dockerfile 裡的指令
（uvicorn web.app:app --host 0.0.0.0），那條路徑沒有 LOCAL_ONLY，
沒設 STOCKCORE_PASSWORD 就會整個服務拒絕回應。詳見 docs/DEPLOY.md。
"""

import os

import uvicorn

if __name__ == "__main__":
    os.environ.setdefault("STOCKCORE_LOCAL_ONLY", "1")
    port = int(os.getenv("STOCKCORE_WEB_PORT", "8000"))
    print(f"stockcore 網站版：http://127.0.0.1:{port}　（僅本機可連，Ctrl+C 結束）")
    uvicorn.run("web.app:app", host="127.0.0.1", port=port, reload=False)
