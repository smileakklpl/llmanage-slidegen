"""Regression tests for matching PowerPoint charts to audit worksheets."""

from ppt_generation.verification import verify_chart_consistency as vcc


def _sheet(*, title: str, metric_key: str, page: int) -> vcc.ExternalSheet:
    return vcc.ExternalSheet(
        sheet_name=f"P.{page}_{metric_key}",
        chart_title=title,
        metric_key=metric_key,
        page_number=page,
    )


def test_duplicate_chart_titles_match_by_actual_slide_number():
    """A value chart must not be matched to a rank sheet with the same title."""
    rank = _sheet(
        title="各銀行流通卡數排名",
        metric_key="流通卡數.rank",
        page=8,
    )
    value = _sheet(
        title="各銀行流通卡數排名",
        metric_key="流通卡數.value",
        page=11,
    )

    matched = vcc._match_external_sheet(
        [rank, value],
        "各銀行流通卡數排名",
        slide_number=11,
        chart_ordinal=7,
    )

    assert matched is value


def test_unique_chart_title_remains_primary_when_page_numbers_are_legacy():
    """Keep compatibility with workbooks whose logical page differs from slide."""
    expected = _sheet(
        title="市場流通卡數趨勢",
        metric_key="流通卡數.value",
        page=4,
    )
    unrelated = _sheet(
        title="同業排名",
        metric_key="流通卡數.rank",
        page=5,
    )

    matched = vcc._match_external_sheet(
        [expected, unrelated],
        "市場流通卡數趨勢",
        slide_number=6,
        chart_ordinal=1,
    )

    assert matched is expected
