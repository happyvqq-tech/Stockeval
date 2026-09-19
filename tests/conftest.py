import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    """每個測試一律用獨立的快取目錄，絕對不准碰專案根目錄的真實 ./cache。

    沒有這層隔離，任何跑過 cli.py / run_demo.py 手動示範、或忘記清理的
    ./cache 殘留檔案，會讓測試的行為取決於「這台機器之前跑過什麼」——
    結果就是自己電腦上過、CI 上炸、或反過來，而且非常難查。
    這個 autouse fixture 放在 conftest.py，全部測試檔自動套用，不用
    每個測試檔各自記得寫一次。
    """
    monkeypatch.setenv("STOCKCORE_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("STOCKCORE_NO_CACHE", raising=False)
