"""編輯 config/universe/<市場>.txt 的股票池。

只碰這個設定檔本身：路徑走 data.universe.path()，讀取走
data.universe.load()，不在這裡重寫一份路徑規則或解析邏輯。
"""

from __future__ import annotations

from data.universe import load, path


def _existing_codes(market: str) -> set[str]:
    try:
        return {c for c, _ in load(market)}
    except FileNotFoundError:
        return set()


def backup(market: str) -> None:
    p = path(market)
    if p.exists():
        p.with_suffix(".txt.bak").write_text(p.read_text(encoding="utf-8"), encoding="utf-8")


def add(market: str, code: str, name: str = "") -> None:
    code = code.strip()
    if not code:
        raise ValueError("代號不可為空")
    if code in _existing_codes(market):
        raise ValueError(f"{code} 已在股票池中")

    backup(market)
    p = path(market)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(f"{code} {name.strip()}\n")


def remove(market: str, code: str) -> None:
    code = code.strip()
    p = path(market)
    if not p.exists():
        return

    backup(market)
    kept = []
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            c, _, _ = stripped.partition(" ")
            if c.strip() == code:
                continue                    # 這行就是要刪的，跳過
        kept.append(line)
    p.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
