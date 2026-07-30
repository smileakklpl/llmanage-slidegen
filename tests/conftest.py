"""pytest 進入點：把 src/ 放進 import 路徑（見 scripts/bootstrap.py）。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import bootstrap  # noqa: E402,F401

# bootstrap 只掛 `src/core/`（那是 core 的 import 根目錄）。`ppt_generation`
# 的 import 根目錄是 `src/`——它平時的執行方式就是 `cd src` 後
# `python -m ppt_generation.run_pipeline`，這裡對齊同一個根目錄，
# 測試裡的 import 才會與產品碼一致。
_SRC = str(REPO_ROOT / "src")

if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
