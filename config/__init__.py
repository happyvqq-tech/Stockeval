"""設定載入層。

core/ 不寫任何 `if market == "TW"`，一律用 market_cfg(market) 取值，
所以新增市場只要在 markets.yaml 加一段，程式碼不用改。
"""

from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path

import yaml

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    with open(_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def markets() -> dict:
    return copy.deepcopy(_load("markets.yaml"))


def rules() -> dict:
    return copy.deepcopy(_load("rules.yaml"))


def market_cfg(market: str) -> dict:
    cfg = _load("markets.yaml")
    key = (market or "").upper()
    if key not in cfg:
        raise KeyError(f"未知市場 {market!r}，可用：{sorted(cfg)}")
    return copy.deepcopy(cfg[key])
