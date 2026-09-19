"""背景工作管理：推薦排序掃描、復盤都在這裡跑。

掃整個股票池動輒 30 秒以上（FinMind 免費層還故意 sleep 0.3 秒/檔），
不能讓 HTTP 請求空等那麼久，所以丟到背景執行緒，前端輪詢進度。復盤
更慢——每一檔要抓兩段時間的資料（as_of 之前、之後），一樣用這套機制。

同一時間只准一個工作在跑（掃描或復盤都算），共用同一把鎖 —— 避免兩個
請求同時打 API 觸發速率限制。"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from . import logic

_lock = threading.Lock()               # 保護下面兩個 dict 本身
_jobs: dict[str, "ScanJob"] = {}
_review_jobs: dict[str, "ReviewJob"] = {}
_scan_slot = threading.Lock()          # 同一時間只准一個掃描或復盤在跑


@dataclass
class ScanJob:
    id: str
    market: str
    symbols: list
    status: str = "running"            # running | done | error
    done: int = 0
    total: int = 0
    as_of: str | None = None
    ranked: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    any_unadjusted: bool = False
    error: str | None = None
    started_at: float = field(default_factory=time.time)


def create(market: str, symbols: list[str]) -> ScanJob:
    job = ScanJob(id=uuid.uuid4().hex[:10], market=market, symbols=symbols, total=len(symbols))
    with _lock:
        _jobs[job.id] = job
    return job


def get(job_id: str) -> ScanJob | None:
    with _lock:
        return _jobs.get(job_id)


def start(job: ScanJob, *, top: int | None = None, min_score: float = 0.0) -> bool:
    """啟動背景執行緒，立刻返回。若已有掃描在跑，這個 job 直接標記失敗。"""
    if not _scan_slot.acquire(blocking=False):
        job.status = "error"
        job.error = "已有掃描正在進行，請稍候再試"
        return False
    thread = threading.Thread(target=_run, args=(job, top, min_score), daemon=True)
    thread.start()
    return True


def _run(job: ScanJob, top, min_score) -> None:
    try:
        def progress(done, total):
            job.done = done

        result = logic.run_scan(job.market, job.symbols, top=top, min_score=min_score,
                                on_progress=progress)
        job.ranked = result["ranked"]
        job.failed = result["failed"]
        job.any_unadjusted = result["any_unadjusted"]
        job.as_of = job.ranked[0]["as_of"] if job.ranked else None
        job.status = "done"
    except Exception as e:                    # 整批性質的失敗（例如股票池讀取失敗）
        job.status = "error"
        job.error = str(e)
    finally:
        _scan_slot.release()


@dataclass
class ReviewJob:
    id: str
    market: str
    symbols: list
    asof: str
    until: str | None = None
    status: str = "running"            # running | done | error
    done: int = 0
    total: int = 0
    result: dict | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)


def create_review(market: str, symbols: list[str], *, asof: str, until: str | None) -> ReviewJob:
    job = ReviewJob(id=uuid.uuid4().hex[:10], market=market, symbols=symbols,
                    asof=asof, until=until, total=len(symbols))
    with _lock:
        _review_jobs[job.id] = job
    return job


def get_review(job_id: str) -> ReviewJob | None:
    with _lock:
        return _review_jobs.get(job_id)


def start_review(job: ReviewJob, *, top: int | None = None) -> bool:
    if not _scan_slot.acquire(blocking=False):
        job.status = "error"
        job.error = "已有掃描或復盤正在進行，請稍候再試"
        return False
    thread = threading.Thread(target=_run_review, args=(job, top), daemon=True)
    thread.start()
    return True


def _run_review(job: ReviewJob, top) -> None:
    try:
        def progress(done, total):
            job.done = done

        job.result = logic.run_review(job.market, job.symbols, asof=job.asof,
                                      until=job.until, top=top, on_progress=progress)
        job.status = "done"
    except Exception as e:
        job.status = "error"
        job.error = str(e)
    finally:
        _scan_slot.release()
