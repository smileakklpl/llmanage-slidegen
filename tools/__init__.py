"""從 repo root 執行時，補上 src/ 的 import 路徑（見 scripts/bootstrap.py）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import bootstrap  # noqa: E402,F401
