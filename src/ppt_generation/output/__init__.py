"""
檔案產出（Stage 5-6）
======================
- :mod:`renderer`       ── 套用 template.pptx 組頁、插入原生圖表、代入敘事佔位符
- :mod:`excel_exporter` ── FR-3 外部稽核 `.xlsx`，含來源儲存格與索引頁

兩者吃**同一份 ChartSpec**，因此 PPT 與 Excel 的數字必然相同。
"""

from __future__ import annotations

from .excel_exporter import (
    ExportReport,
    export_audit_workbook,
    sheet_name_for,
)
from .renderer import (
    PageBundle,
    RenderError,
    RenderReport,
    add_content_page,
    add_section_divider,
    render_deck,
    scatter_labels_pending,
)

__all__ = [
    "ExportReport",
    "PageBundle",
    "RenderError",
    "RenderReport",
    "add_content_page",
    "add_section_divider",
    "export_audit_workbook",
    "render_deck",
    "scatter_labels_pending",
    "sheet_name_for",
]
