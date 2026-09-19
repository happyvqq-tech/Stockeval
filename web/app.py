"""FastAPI 路由層。

只做三件事：接請求、呼叫 web/logic.py 或 web/jobs.py、把結果丟給模板。
不在這裡計算任何指標或門檻 —— 那是 core/ 的事。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from data import universe

from . import auth, jobs, logic, universe_edit
from .auth import AuthMiddleware, RateLimitMiddleware

# 用絕對路徑：部署時的工作目錄不一定是專案根目錄
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _access_label() -> str:
    """導覽列右上角的狀態文字。用函式而不是固定字串，因為部署到 Render 之後
    這裡本來寫死顯示「僅本機自用」，跟實際狀態不符 —— 已加密碼保護還這樣講
    會讓人誤以為服務沒對外開放。"""
    if auth.local_only():
        return "僅本機自用"
    if auth.configured_password():
        return "已啟用密碼保護"
    return "⚠ 未設定密碼"          # 理論上進不到這裡，fail-closed 會先擋下


templates.env.globals["access_label"] = _access_label

MARKETS = ["TW", "US", "CN"]

app = FastAPI(title="stockcore")

# 順序重要：後加的先執行。速率限制要擋在驗證之前，
# 否則有人可以靠不斷送錯密碼來耗資源。
app.add_middleware(AuthMiddleware)
app.add_middleware(RateLimitMiddleware)


@app.get("/healthz")
def healthz():
    """平台健康檢查用。不吐任何資料，所以免驗證。"""
    return {"status": "ok"}


def _names(market: str) -> dict[str, str]:
    try:
        return universe.names(market)
    except FileNotFoundError:
        return {}


# -------------------------------------------------------------- 推薦排序
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"markets": MARKETS})


@app.post("/api/scan", response_class=HTMLResponse)
def api_scan(request: Request, market: str = Form(...), symbols: str = Form(""),
             top: str = Form(""), min_score: str = Form("0")):
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        try:
            syms = universe.symbols(market)
        except FileNotFoundError as e:
            return templates.TemplateResponse(request, "_scan_error.html", {"error": str(e)})

    job = jobs.create(market, syms)
    top_n = int(top) if top.strip() else None
    min_s = float(min_score) if min_score.strip() else 0.0
    jobs.start(job, top=top_n, min_score=min_s)

    return templates.TemplateResponse(
        request, "_scan_status.html", {"job": job, "names": _names(market)})


@app.get("/api/scan/{job_id}", response_class=HTMLResponse)
def api_scan_status(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "_scan_error.html", {"error": "找不到這個掃描工作（可能已過期）"})
    return templates.TemplateResponse(
        request, "_scan_status.html", {"job": job, "names": _names(job.market)})


# -------------------------------------------------------------- 個股檢查
@app.get("/check", response_class=HTMLResponse)
def check_form(request: Request):
    return templates.TemplateResponse(
        request, "check.html", {"markets": MARKETS, "result": None, "error": None})


@app.post("/check", response_class=HTMLResponse)
def check_submit(request: Request, market: str = Form(...), symbol: str = Form(...),
                  unrealized: float = Form(0.0)):
    result, error = None, None
    try:
        result = logic.run_check(market, symbol.strip(), unrealized)
    except Exception as e:
        error = str(e)

    return templates.TemplateResponse(request, "check.html", {
        "markets": MARKETS, "result": result, "error": error,
        "market": market, "symbol": symbol, "unrealized": unrealized,
    })


# -------------------------------------------------------------- 股票池管理
@app.get("/universe", response_class=HTMLResponse)
def universe_page(request: Request, market: str = "TW", error: str | None = None):
    try:
        rows = universe.load(market)
    except FileNotFoundError as e:
        rows, error = [], error or str(e)
    return templates.TemplateResponse(
        request, "universe.html",
        {"markets": MARKETS, "market": market, "rows": rows, "error": error})


@app.post("/universe/add")
def universe_add(market: str = Form(...), code: str = Form(...), name: str = Form("")):
    error = None
    try:
        universe_edit.add(market, code, name)
    except ValueError as e:
        error = str(e)
    url = f"/universe?market={market}"
    if error:
        url += f"&error={quote(error)}"
    return RedirectResponse(url, status_code=303)


@app.post("/universe/remove")
def universe_remove(market: str = Form(...), code: str = Form(...)):
    universe_edit.remove(market, code)
    return RedirectResponse(f"/universe?market={market}", status_code=303)
