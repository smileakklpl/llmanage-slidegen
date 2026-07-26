"""把 `src/` 放進 import 路徑。

專案分成兩層：`src/` 是產品碼（依規格書 §4.3 的模組切分），
`evalh/`、`tools/`、`tests/` 是量測與工具，放在 repo root。

兩層要互相 import，最省事的做法是讓 `src/` 也成為 import 根目錄——
這樣 `from engine.reader import ...` 在哪裡都寫得一樣，
不必到處寫 `from src.engine.reader import ...`，
之後模組搬家時也不用改一堆 import。

一律**從 repo root 執行**：

    python verify_all.py
    python -m evalh.harness --stage writer
    python -m tools.spike_a --provider mock

repo root 由 Python 自動放進 sys.path（cwd），這支只補上 `src/`。

順帶把 stdout/stderr 釘成 UTF-8。報表用的 ✓ ✗ 和全部的中文在
繁中 Windows 的預設 cp950 下編不出來，一旦輸出被導向管線或檔案
（CI log、`python verify_all.py > out.txt`）就會 UnicodeEncodeError 中止——
訊息還會蓋掉真正的失敗原因。互動式終端機看不出來，所以特別容易漏。
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

for _p in (REPO_ROOT, REPO_ROOT / "src"):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")
