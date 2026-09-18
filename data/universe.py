"""股票池 —— 掃描要有對象，這裡提供清單。

清單放在 config/universe/<MARKET>.txt，一行一檔：「代號 名稱」，# 為註解。
這些是起手用的參考清單，不是即時成分股，請自行維護。
"""

from __future__ import annotations

from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "config" / "universe"


def available() -> list[str]:
    return sorted(p.stem for p in _DIR.glob("*.txt"))


def load(market: str) -> list[tuple[str, str]]:
    """回傳 [(代號, 名稱), ...]。"""
    p = _DIR / f"{market.upper()}.txt"
    if not p.exists():
        raise FileNotFoundError(
            f"找不到 {market} 的股票池：{p}\n"
            f"目前有：{available() or '（無）'}。可自行新增這個檔案。"
        )
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        code, _, name = line.partition(" ")
        out.append((code.strip(), name.strip()))
    return out


def symbols(market: str) -> list[str]:
    return [c for c, _ in load(market)]


def names(market: str) -> dict[str, str]:
    return {c: n for c, n in load(market)}
