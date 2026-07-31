"""把產品碼根目錄 ``src/`` 加入 Python import 路徑。

所有開發指令一律從 repo root 執行；Python 已自動把 repo root 放進
``sys.path``，這裡只補上產品碼根目錄，讓正式套件能以
``core.*``、``backend.*``、``ppt_generation.*`` 匯入。

同時將 stdout/stderr 設為 UTF-8，避免 Windows cp950 無法輸出中文與驗收符號。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"

_src = str(SRC_ROOT)
if _src not in sys.path:
    sys.path.insert(0, _src)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")
