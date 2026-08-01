from datetime import datetime
from typing import Any
from enum import Enum

from pydantic import BaseModel, Field


class ContainerType(str, Enum):
    """檔案最外層的容器格式。"""

    XLSX = "xlsx"
    XLS = "xls"
    CSV = "csv"
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    PPTX = "pptx"
    DOCX = "docx"
    ZIP = "zip"
    UNKNOWN = "unknown"


class SheetContentType(str, Enum):
    """Excel 工作表的主要內容類型。"""
    CHART_IMAGE = "chart_image"
    DOCUMENT_TEXT = "document_text"
    EMPTY = "empty"
    STRUCTURED_TABLE = "structured_table"
    FINANCIAL_STATEMENT = "financial_statement"
    NATIVE_CHART = "native_chart"
    EMBEDDED_IMAGE = "embedded_image"
    MIXED_CONTENT = "mixed_content"
    UNKNOWN = "unknown"


class FinancialStatementSubtype(str, Enum):
    """財務報表的細分類型。"""

    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW_STATEMENT = "cash_flow_statement"
    UNKNOWN = "unknown"


class FileInspectionResult(BaseModel):
    """第一階段：檔案格式偵測結果。"""

    filename: str
    extension: str | None = None
    detected_container_type: ContainerType
    mime_type: str
    size_bytes: int = Field(ge=0)

    extension_matches_content: bool | None = None
    is_readable: bool
    is_encrypted: bool = False

    warnings: list[str] = Field(default_factory=list)


class SheetContentInspection(BaseModel):
    """單一 Excel 工作表的內容分類結果。"""

    sheet_name: str
    sheet_state: str

    primary_content_type: SheetContentType
    components: list[SheetContentType] = Field(default_factory=list)

    financial_statement_subtype: (
        FinancialStatementSubtype | None
    ) = None

    confidence: float = Field(ge=0.0, le=1.0)

    max_row: int = Field(ge=0)
    max_column: int = Field(ge=0)

    non_empty_cells: int = Field(ge=0)
    text_cells: int = Field(ge=0)
    numeric_cells: int = Field(ge=0)
    formula_cells: int = Field(ge=0)
    date_cells: int = Field(ge=0)

    merged_range_count: int = Field(ge=0)
    chart_count: int = Field(ge=0)
    image_count: int = Field(ge=0)

    detected_header_row: int | None = None
    structured_table_score: float = Field(ge=0.0, le=1.0)
    financial_statement_score: float = Field(ge=0.0, le=1.0)

    evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WorkbookContentInspection(BaseModel):
    """第二階段：整份 Excel 的內容分類結果。"""

    filename: str
    sheet_count: int = Field(ge=0)
    overall_content_type: SheetContentType
    confidence: float = Field(ge=0.0, le=1.0)

    sheets: list[SheetContentInspection] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(default_factory=list)


class ColumnDataType(str, Enum):
    """抽取後的欄位資料型態。"""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    MIXED = "mixed"
    EMPTY = "empty"


class SourceCell(BaseModel):
    """資料原始來源位置。"""

    sheet: str
    cell: str
    row: int = Field(ge=1)
    column: int = Field(ge=1)


class ExtractedCell(BaseModel):
    """抽取後的單一儲存格。"""

    raw_value: Any = None
    value: Any = None

    formula: str | None = None
    number_format: str | None = None

    source: SourceCell
    sources: list[SourceCell] = Field(
        default_factory=list
    )
    transformations: list[str] = Field(
        default_factory=list
    )


class TableColumnSpec(BaseModel):
    """表格欄位定義。"""

    key: str
    label: str
    index: int = Field(ge=0)

    data_type: ColumnDataType
    unit: str | None = None
    nullable: bool = True

    header_source: SourceCell
    header_sources: list[SourceCell] = Field(
        default_factory=list
    )


class TableMetadata(BaseModel):
    """表格前置資訊。"""

    title: str | None = None
    entity: str | None = None
    unit: str | None = None

    notes: list[str] = Field(default_factory=list)


class ExtractedTableRow(BaseModel):
    """抽取後的一列資料。"""

    excel_row: int = Field(ge=1)
    cells: dict[str, ExtractedCell]


class TableDatasetSpec(BaseModel):
    """單一 Excel 工作表抽取後的標準格式。"""

    filename: str
    sheet_name: str

    table_kind: SheetContentType
    financial_statement_subtype: (
        FinancialStatementSubtype | None
    ) = None

    metadata: TableMetadata

    header_row: int = Field(ge=1)
    header_range: str
    data_range: str | None = None
    full_range: str

    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)

    columns: list[TableColumnSpec]
    rows: list[ExtractedTableRow]

    # Deterministic layout evidence is persisted as JSON so Refresh can
    # replay the same normalization plan without reinterpreting the sheet.
    layout_strategy: str | None = None
    layout_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    normalization_spec: dict[str, Any] | None = None

    warnings: list[str] = Field(default_factory=list)


class WorkbookTableExtraction(BaseModel):
    """整份 Excel 的資料表抽取結果。"""

    filename: str
    table_count: int = Field(ge=0)

    tables: list[TableDatasetSpec] = Field(
        default_factory=list
    )

    skipped_sheets: list[str] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(default_factory=list) 

class QualitySeverity(str, Enum):
    """資料品質問題的嚴重程度。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class QualityStatus(str, Enum):
    """資料品質驗證結果。"""

    PASS = "pass"
    WARNING = "warning"
    FAIL = "fail"


class QualityIssue(BaseModel):
    """單一資料品質問題。"""

    code: str
    severity: QualitySeverity
    message: str

    sheet_name: str
    column_key: str | None = None
    cells: list[str] = Field(default_factory=list)

    details: dict[str, Any] = Field(
        default_factory=dict
    )


class ColumnQualitySummary(BaseModel):
    """單一欄位的品質摘要。"""

    key: str
    label: str

    row_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    missing_rate: float = Field(ge=0.0, le=1.0)

    invalid_type_count: int = Field(ge=0)
    unique_count: int = Field(ge=0)

    expected_data_type: ColumnDataType


class TableQualityReport(BaseModel):
    """單張表格的品質驗證報告。"""

    sheet_name: str
    status: QualityStatus
    score: float = Field(ge=0.0, le=100.0)

    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)

    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    info_count: int = Field(ge=0)

    columns: list[ColumnQualitySummary] = Field(
        default_factory=list
    )

    issues: list[QualityIssue] = Field(
        default_factory=list
    )


class WorkbookQualityReport(BaseModel):
    """整份 Excel 的品質驗證報告。"""

    filename: str
    status: QualityStatus
    score: float = Field(ge=0.0, le=100.0)

    table_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    info_count: int = Field(ge=0)

    tables: list[TableQualityReport] = Field(
        default_factory=list
    )
class PipelineStatus(str, Enum):
    """整條資料處理管線的最終狀態。"""

    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    UNSUPPORTED = "unsupported"
    REJECTED = "rejected"
    FAILED = "failed"


class PipelineStageStatus(str, Enum):
    """單一處理階段的執行狀態。"""

    COMPLETED = "completed"
    WARNING = "warning"
    SKIPPED = "skipped"
    FAILED = "failed"


class PipelineStageResult(BaseModel):
    """單一 Pipeline 階段的執行資訊。"""

    stage: str
    status: PipelineStageStatus
    message: str

    duration_ms: float = Field(ge=0.0)

class PdfPageContent(BaseModel):
    """單一 PDF 頁面的文字與結構資訊。"""

    page_number: int = Field(ge=1)

    width: float = Field(ge=0.0)
    height: float = Field(ge=0.0)

    text: str
    text_char_count: int = Field(ge=0)
    word_count: int = Field(ge=0)
    text_truncated: bool = False

    table_count: int = Field(ge=0)
    image_count: int = Field(ge=0)

    has_text_layer: bool
    likely_scanned: bool

    financial_statement_subtype: (
        FinancialStatementSubtype | None
    ) = None

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    warnings: list[str] = Field(
        default_factory=list
    )


class PdfDocumentContent(BaseModel):
    """整份 PDF 的文字層解析結果。"""

    filename: str

    page_count: int = Field(ge=0)
    processed_page_count: int = Field(ge=0)

    has_text_layer: bool
    scanned_page_count: int = Field(ge=0)

    total_text_char_count: int = Field(ge=0)

    full_text: str
    text_truncated: bool = False

    pages: list[PdfPageContent] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(
        default_factory=list
    )

class VisualTextBlock(BaseModel):
    """OCR 辨識到的一段文字。"""

    text: str
    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    bbox: list[float] = Field(
        default_factory=list
    )

    label: str | None = None


class VisualTableContent(BaseModel):
    """圖片中偵測到的表格摘要。"""

    page_number: int = Field(ge=1)
    table_index: int = Field(ge=1)

    html: str | None = None

    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    bbox: list[float] = Field(
        default_factory=list
    )


class VisualPageContent(BaseModel):
    """單一圖片或掃描頁的辨識結果。"""

    page_number: int = Field(ge=1)

    width: int = Field(ge=0)
    height: int = Field(ge=0)

    primary_content_type: SheetContentType

    components: list[SheetContentType] = Field(
        default_factory=list
    )

    ocr_text: str

    average_ocr_confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    text_blocks: list[VisualTextBlock] = Field(
        default_factory=list
    )

    tables: list[VisualTableContent] = Field(
        default_factory=list
    )

    chart_count: int = Field(ge=0)

    chart_contents: list[str] = Field(
        default_factory=list
    )

    likely_scanned: bool = True
    requires_human_review: bool = False

    warnings: list[str] = Field(
        default_factory=list
    )


class VisualDocumentContent(BaseModel):
    """整份圖片或掃描文件的辨識結果。"""

    filename: str
    engine: str

    page_count: int = Field(ge=0)
    processed_page_count: int = Field(ge=0)

    average_ocr_confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    chart_recognition_enabled: bool
    requires_human_review: bool

    pages: list[VisualPageContent] = Field(
        default_factory=list
    )

    warnings: list[str] = Field(
        default_factory=list
    )

class SourceKind(str, Enum):
    """統一資料集的來源類型。"""

    EXCEL = "excel"
    DELIMITED_TEXT = "delimited_text"
    PDF_TEXT_TABLE = "pdf_text_table"
    OCR_IMAGE_TABLE = "ocr_image_table"
    OCR_PDF_TABLE = "ocr_pdf_table"
    UNKNOWN = "unknown"


class EvidenceType(str, Enum):
    """來源證據的粒度。"""

    FILE = "file"
    TABLE_RANGE = "table_range"
    CELL = "cell"
    PAGE = "page"
    OCR_REGION = "ocr_region"
    HUMAN_REVIEW = "human_review"


class ReviewStatus(str, Enum):
    """資料集的人工確認狀態。"""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class HumanReviewDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class SourceEvidence(BaseModel):
    """一筆資料或資料集的來源證據。"""

    evidence_type: EvidenceType
    source_kind: SourceKind

    filename: str

    sheet_name: str | None = None
    page_number: int | None = Field(
        default=None,
        ge=1,
    )
    table_index: int | None = Field(
        default=None,
        ge=1,
    )

    cell: str | None = None
    cell_range: str | None = None

    bbox: list[float] = Field(
        default_factory=list
    )

    extraction_method: str

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    note: str | None = None


class UnifiedDataValue(BaseModel):
    """統一資料列中的單一欄位值。"""

    raw_value: Any | None = None
    value: Any | None = None

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    evidence: list[SourceEvidence] = Field(
        default_factory=list
    )

    requires_human_review: bool = False


class UnifiedDatasetRecord(BaseModel):
    """統一資料集中的一列資料。"""

    record_index: int = Field(ge=0)
    source_row: int | None = Field(
        default=None,
        ge=1,
    )

    values: dict[
        str,
        UnifiedDataValue,
    ] = Field(default_factory=dict)

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    requires_human_review: bool = False


class UnifiedDatasetSpec(BaseModel):
    """
    提供給後續分析與簡報生成器使用的統一資料格式。
    """

    dataset_id: str

    name: str
    filename: str

    source_container_type: ContainerType
    source_kind: SourceKind

    table_kind: SheetContentType

    financial_statement_subtype: (
        FinancialStatementSubtype | None
    ) = None

    metadata: TableMetadata

    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)

    columns: list[TableColumnSpec] = Field(
        default_factory=list
    )

    records: list[
        UnifiedDatasetRecord
    ] = Field(default_factory=list)

    confidence: float = Field(
        ge=0.0,
        le=1.0,
    )

    requires_human_review: bool = False

    review_status: ReviewStatus = (
        ReviewStatus.NOT_REQUIRED
    )

    review_reasons: list[str] = Field(
        default_factory=list
    )

    evidence: list[SourceEvidence] = Field(
        default_factory=list
    )

    layout_strategy: str | None = None
    layout_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
    )
    normalization_spec: dict[str, Any] | None = None

    warnings: list[str] = Field(
        default_factory=list
    )

    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_notes: str | None = None


class DatasetCorrection(BaseModel):
    """人工確認時修改某一格資料。"""

    record_index: int = Field(ge=0)
    column_key: str = Field(min_length=1)

    corrected_value: Any

    note: str | None = None


class HumanReviewRequest(BaseModel):
    """人工確認決策。"""

    decision: HumanReviewDecision

    reviewer: str = Field(
        min_length=1,
    )

    notes: str | None = None

    corrections: list[
        DatasetCorrection
    ] = Field(default_factory=list)


class DatasetReviewPayload(BaseModel):
    """人工確認 API 的請求格式。"""

    dataset: UnifiedDatasetSpec
    review: HumanReviewRequest

class UnifiedIngestionResult(BaseModel):
    filename: str
    pipeline_status: PipelineStatus

    inspection: FileInspectionResult | None = None

    classification: (
        WorkbookContentInspection | None
    ) = None

    document: PdfDocumentContent | None = None
    visual: VisualDocumentContent | None = None

    extraction: (
        WorkbookTableExtraction | None
    ) = None

    validation: (
        WorkbookQualityReport | None
    ) = None

    datasets: list[
        UnifiedDatasetSpec
    ] = Field(default_factory=list)

    review_required_count: int = Field(
        default=0,
        ge=0,
    )

    stages: list[
        PipelineStageResult
    ] = Field(default_factory=list)

    warnings: list[str] = Field(
        default_factory=list
    )

    errors: list[str] = Field(
        default_factory=list
    )