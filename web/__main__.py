"""啟動：python -m web

僅供本機自用，只綁 127.0.0.1，不對外開放。要換 port 設環境變數
STOCKCORE_WEB_PORT（預設 8000）。
"""

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.getenv("STOCKCORE_WEB_PORT", "8000"))
    print(f"stockcore 網站版：http://127.0.0.1:{port}　（僅本機可連，Ctrl+C 結束）")
    uvicorn.run("web.app:app", host="127.0.0.1", port=port, reload=False)
