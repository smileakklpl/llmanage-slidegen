"""pytest 進入點：把正式產品碼根目錄 src/ 放進 import 路徑。"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_ROOT = str(REPO_ROOT / "scripts")

if SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, SCRIPTS_ROOT)

import bootstrap  # noqa: E402,F401
