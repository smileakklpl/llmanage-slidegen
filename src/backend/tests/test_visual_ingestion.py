from pathlib import Path

from PIL import Image

from app.ingestion.schemas import (
    SheetContentType,
)
from app.ingestion.visual_parser import (
    inspect_visual_input,
)


class FakeResult:
    def __init__(
        self,
        payload: dict,
    ) -> None:
        self.json = payload


class FakeEngine:
    def __init__(
        self,
        payload: dict,
    ) -> None:
        self.payload = payload

    def predict(self, input_path: str):
        return [
            FakeResult(self.payload)
        ]


def _create_image(
    file_path: Path,
) -> None:
    image = Image.new(
        "RGB",
        (800, 600),
        "white",
    )

    image.save(file_path)


def test_text_image(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "document.png"
    _create_image(file_path)

    engine = FakeEngine({
        "width": 800,
        "height": 600,
        "parsing_res_list": [
            {
                "block_label": "text",
                "block_content":
                    "Quarterly report",
            }
        ],
        "overall_ocr_res": {
            "rec_texts": [
                "Quarterly report",
                "Revenue increased",
            ],
            "rec_scores": [
                0.98,
                0.96,
            ],
            "rec_polys": [],
        },
        "table_res_list": [],
    })

    classification, visual, extraction = (
        inspect_visual_input(
            file_path,
            engine=engine,
        )
    )

    assert (
        classification
        .overall_content_type
        == SheetContentType.DOCUMENT_TEXT
    )

    assert visual.page_count == 1
    assert extraction.table_count == 0


def test_table_image(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "table.png"
    _create_image(file_path)

    engine = FakeEngine({
        "width": 800,
        "height": 600,
        "parsing_res_list": [
            {
                "block_label": "table",
                "block_content": "",
            }
        ],
        "overall_ocr_res": {
            "rec_texts": [
                "月份",
                "營收",
                "2026-01",
                "100000",
            ],
            "rec_scores": [
                0.95,
                0.95,
                0.93,
                0.94,
            ],
            "rec_polys": [],
        },
        "table_res_list": [
            {
                "pred_html": (
                    "<table>"
                    "<tr>"
                    "<th>月份</th>"
                    "<th>營收</th>"
                    "</tr>"
                    "<tr>"
                    "<td>2026-01</td>"
                    "<td>100000</td>"
                    "</tr>"
                    "<tr>"
                    "<td>2026-02</td>"
                    "<td>120000</td>"
                    "</tr>"
                    "</table>"
                ),
                "table_ocr_pred": {
                    "rec_scores": [
                        0.95,
                        0.94,
                    ]
                },
            }
        ],
    })

    classification, _, extraction = (
        inspect_visual_input(
            file_path,
            engine=engine,
        )
    )

    assert (
        classification
        .overall_content_type
        == SheetContentType
        .STRUCTURED_TABLE
    )

    assert extraction.table_count == 1

    table = extraction.tables[0]

    assert table.row_count == 2

    assert (
        table.rows[0]
        .cells["營收"]
        .value
        == 100000
    )


def test_chart_image_requires_review(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "chart.png"
    _create_image(file_path)

    engine = FakeEngine({
        "width": 800,
        "height": 600,
        "parsing_res_list": [
            {
                "block_label": "chart",
                "block_content":
                    "Revenue chart",
            }
        ],
        "overall_ocr_res": {
            "rec_texts": [
                "Revenue",
                "2025",
                "2026",
            ],
            "rec_scores": [
                0.95,
                0.95,
                0.95,
            ],
            "rec_polys": [],
        },
        "table_res_list": [],
    })

    classification, visual, extraction = (
        inspect_visual_input(
            file_path,
            engine=engine,
        )
    )

    assert (
        classification
        .overall_content_type
        == SheetContentType.CHART_IMAGE
    )

    assert (
        visual.requires_human_review
        is True
    )

    assert extraction.table_count == 0