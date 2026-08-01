"""Deterministic cell tokenization and accounting-value assembly."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Sequence

from openpyxl.utils import get_column_letter

from app.ingestion.classifier import is_period_like_header
from app.ingestion.workbook_ir import SheetIR


INVISIBLE_SPACE_PATTERN = re.compile(
    r"[\s\u2000-\u200f\u2028-\u202f\u205f\u2060\u3000\ufeff]+"
)
NUMERIC_PATTERN = re.compile(
    r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$"
)
ACCOUNTING_PATTERN = re.compile(
    r"^\(([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\)$"
)
OPEN_ACCOUNTING_PATTERN = re.compile(
    r"^\(([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)$"
)
CLOSE_ACCOUNTING_PATTERN = re.compile(r"^\)+$")
YEAR_PATTERN = re.compile(r"(?<!\d)(19\d{2}|20\d{2}|21\d{2}|2200)(?!\d)")
UNIT_PATTERN = re.compile(
    r"(?:amounts?\s+)?in\s+thousands|單位\s*[:：]|unit\s*[:：]",
    flags=re.IGNORECASE,
)
EMPTY_MARKERS = {"", "-", "--", "—", "–", "N/A", "NA", "不適用"}


class TokenKind(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    ACCOUNTING_NUMBER = "accounting_number"
    ACCOUNTING_FRAGMENT = "accounting_fragment"
    PERIOD = "period"
    CURRENCY = "currency"
    UNIT = "unit"
    MARKER = "marker"


@dataclass(frozen=True)
class TokenSource:
    row: int
    column: int
    cell: str
    raw_value: Any


@dataclass(frozen=True)
class CellToken:
    row: int
    min_column: int
    max_column: int
    kind: TokenKind
    value: Any
    text: str
    sources: tuple[TokenSource, ...]
    transformations: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssembledValue:
    raw_value: Any
    value: int | float | None
    source_indexes: tuple[int, ...]
    transformations: tuple[str, ...] = ()


@dataclass(frozen=True)
class TokenizedSheet:
    sheet_name: str
    tokens: tuple[CellToken, ...]
    by_row: dict[int, tuple[CellToken, ...]]

    def row_tokens(self, row: int) -> tuple[CellToken, ...]:
        return self.by_row.get(row, ())


def normalize_visible_text(value: Any) -> str:
    """Collapse Unicode layout whitespace without changing visible text."""
    if value is None:
        return ""
    return INVISIBLE_SPACE_PATTERN.sub(" ", str(value)).strip()


def _compact(value: Any) -> str:
    return INVISIBLE_SPACE_PATTERN.sub("", str(value)).strip()


def _to_number(text: str) -> int | float:
    numeric = float(text.replace(",", ""))
    return int(numeric) if numeric.is_integer() else numeric


def parse_accounting_number(value: Any) -> int | float | None:
    """Parse one complete numeric or parenthesized accounting value."""
    if isinstance(value, bool) or isinstance(value, (date, datetime)):
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None

    text = _compact(value)
    if text.upper() in EMPTY_MARKERS or text.startswith("="):
        return None

    accounting_match = ACCOUNTING_PATTERN.fullmatch(text)
    if accounting_match:
        return -_to_number(accounting_match.group(1))
    if NUMERIC_PATTERN.fullmatch(text):
        return _to_number(text)
    return None


def is_open_accounting_fragment(value: Any) -> bool:
    return isinstance(value, str) and bool(
        OPEN_ACCOUNTING_PATTERN.fullmatch(_compact(value))
    )


def is_close_accounting_fragment(value: Any) -> bool:
    return isinstance(value, str) and bool(
        CLOSE_ACCOUNTING_PATTERN.fullmatch(_compact(value))
    )


def assemble_accounting_values(values: Sequence[Any]) -> AssembledValue:
    """Select the first numeric value, joining adjacent accounting fragments."""
    for index, raw_value in enumerate(values):
        parsed = parse_accounting_number(raw_value)
        if parsed is not None:
            transformations = (
                ("normalize_numeric_text",)
                if isinstance(raw_value, str)
                else ()
            )
            return AssembledValue(
                raw_value=raw_value,
                value=parsed,
                source_indexes=(index,),
                transformations=transformations,
            )

        if (
            is_open_accounting_fragment(raw_value)
            and index + 1 < len(values)
            and is_close_accounting_fragment(values[index + 1])
        ):
            opening = _compact(raw_value)
            closing = _compact(values[index + 1])
            numeric_text = opening[1:]
            return AssembledValue(
                raw_value=f"{opening}{closing}",
                value=-_to_number(numeric_text),
                source_indexes=(index, index + 1),
                transformations=(
                    "join_accounting_fragments",
                    "remove_thousands_separator",
                    "apply_parentheses_negative",
                ),
            )

    return AssembledValue(
        raw_value=None,
        value=None,
        source_indexes=(),
    )


def period_label(value: Any) -> str | None:
    """Return a deterministic period label for numeric or textual dates."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if is_period_like_header(value):
        return str(value)
    if not isinstance(value, str):
        return None

    text = normalize_visible_text(value)
    compact = text.replace("年", "").replace("月", "")
    if compact.isdigit():
        numeric = int(compact)
        if is_period_like_header(numeric):
            return str(numeric)
    year_match = YEAR_PATTERN.search(text)
    return year_match.group(1) if year_match else None


def currency_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact = _compact(value).upper()
    if not compact:
        return None
    if "NT$" in compact or "TWD" in compact or "新臺幣" in compact:
        return "TWD"
    if "US$" in compact or "USD" in compact:
        return "USD"
    if "EUR" in compact or "€" in compact:
        return "EUR"
    if "JPY" in compact or "¥" in compact:
        return "JPY"
    if compact in {"$", "＄"}:
        return "$"
    return None


def _source(row: int, column: int, raw_value: Any) -> TokenSource:
    return TokenSource(
        row=row,
        column=column,
        cell=f"{get_column_letter(column)}{row}",
        raw_value=raw_value,
    )


def _single_token(
    row: int,
    column: int,
    value: Any,
) -> CellToken:
    text = normalize_visible_text(value)
    source = (_source(row, column, value),)
    period = period_label(value)
    if period is not None:
        return CellToken(
            row=row,
            min_column=column,
            max_column=column,
            kind=TokenKind.PERIOD,
            value=period,
            text=text,
            sources=source,
        )

    currency = currency_label(value)
    if currency is not None:
        return CellToken(
            row=row,
            min_column=column,
            max_column=column,
            kind=TokenKind.CURRENCY,
            value=currency,
            text=text,
            sources=source,
        )

    number = parse_accounting_number(value)
    if number is not None:
        kind = (
            TokenKind.ACCOUNTING_NUMBER
            if isinstance(value, str)
            and _compact(value).startswith("(")
            else TokenKind.NUMBER
        )
        return CellToken(
            row=row,
            min_column=column,
            max_column=column,
            kind=kind,
            value=number,
            text=text,
            sources=source,
            transformations=(
                ("normalize_numeric_text",)
                if isinstance(value, str)
                else ()
            ),
        )

    if is_open_accounting_fragment(value):
        kind = TokenKind.ACCOUNTING_FRAGMENT
    elif is_close_accounting_fragment(value):
        kind = TokenKind.MARKER
    elif isinstance(value, str) and UNIT_PATTERN.search(text):
        kind = TokenKind.UNIT
    else:
        kind = TokenKind.TEXT

    return CellToken(
        row=row,
        min_column=column,
        max_column=column,
        kind=kind,
        value=text,
        text=text,
        sources=source,
    )


def tokenize_sheet(sheet: SheetIR) -> TokenizedSheet:
    """Tokenize a sparse SheetIR and assemble adjacent accounting fragments."""
    tokens: list[CellToken] = []
    by_row_cells: dict[int, list[tuple[int, Any]]] = {}
    for (row, column), cell in sheet.cells.items():
        by_row_cells.setdefault(row, []).append((column, cell.value))

    for row, cells in sorted(by_row_cells.items()):
        ordered = sorted(cells)
        index = 0
        while index < len(ordered):
            column, value = ordered[index]
            if (
                is_open_accounting_fragment(value)
                and index + 1 < len(ordered)
                and ordered[index + 1][0] == column + 1
                and is_close_accounting_fragment(ordered[index + 1][1])
            ):
                next_column, closing = ordered[index + 1]
                assembled = assemble_accounting_values([value, closing])
                tokens.append(
                    CellToken(
                        row=row,
                        min_column=column,
                        max_column=next_column,
                        kind=TokenKind.ACCOUNTING_NUMBER,
                        value=assembled.value,
                        text=str(assembled.raw_value),
                        sources=(
                            _source(row, column, value),
                            _source(row, next_column, closing),
                        ),
                        transformations=assembled.transformations,
                    )
                )
                index += 2
                continue

            tokens.append(_single_token(row, column, value))
            index += 1

    by_row: dict[int, tuple[CellToken, ...]] = {}
    for token in tokens:
        by_row.setdefault(token.row, ())
        by_row[token.row] = by_row[token.row] + (token,)

    return TokenizedSheet(
        sheet_name=sheet.title,
        tokens=tuple(tokens),
        by_row=by_row,
    )


def numeric_tokens(tokens: Iterable[CellToken]) -> list[CellToken]:
    return [
        token
        for token in tokens
        if token.kind
        in {
            TokenKind.NUMBER,
            TokenKind.ACCOUNTING_NUMBER,
            TokenKind.PERIOD,
        }
    ]
