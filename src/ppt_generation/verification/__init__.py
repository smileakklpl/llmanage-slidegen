"""
驗證（Stage 7）
================
- :mod:`verify_chart_consistency` ── 三方數值比對（規格書 T1）：
  chart XML 快取 ↔ PPT 內嵌 workbook ↔ 外部稽核 `.xlsx`

可作為 CLI 執行，通過回傳 exit code 0，發現不一致回傳 1::

    python -m ppt_generation.verification.verify_chart_consistency \\
        outputs/deck.pptx outputs/deck_data.xlsx
"""

from __future__ import annotations

from .verify_chart_consistency import (
    ExternalSheet,
    SeriesComparison,
    VerificationReport,
    print_report,
    verify,
)

__all__ = [
    "ExternalSheet",
    "SeriesComparison",
    "VerificationReport",
    "print_report",
    "verify",
]
