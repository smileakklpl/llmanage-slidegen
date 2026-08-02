import { useEffect, useState } from "react";
import type {
  DatasetCorrection,
  UnifiedDatasetSpec,
} from "@/schemas/ingestionSchema";

const PAGE_SIZE = 30;

interface DatasetReviewPanelProps {
  dataset: UnifiedDatasetSpec;
  lowConfidenceThreshold?: number;
  corrections?: DatasetCorrection[];
  onCorrectionsChange?: (corrections: DatasetCorrection[]) => void;
  disabled?: boolean;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function editableValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function confidenceLabel(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

function correctionKey(recordIndex: number, columnKey: string): string {
  return `${recordIndex}::${columnKey}`;
}

function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  return JSON.stringify(left) === JSON.stringify(right);
}

function parseEditedValue(raw: string, dataType?: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === "") return null;

  if (dataType === "integer") {
    const parsed = Number(trimmed.split(",").join(""));
    if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
      throw new Error("請輸入整數");
    }
    return parsed;
  }

  if (dataType === "number") {
    const parsed = Number(trimmed.split(",").join(""));
    if (!Number.isFinite(parsed)) {
      throw new Error("請輸入有效數字");
    }
    return parsed;
  }

  if (dataType === "boolean") {
    const normalized = trimmed.toLowerCase();
    if (["true", "1", "yes", "是"].includes(normalized)) return true;
    if (["false", "0", "no", "否"].includes(normalized)) return false;
    throw new Error("請輸入 true/false、是/否或 1/0");
  }

  return raw;
}

export function DatasetReviewPanel({
  dataset,
  lowConfidenceThreshold = 0.90,
  corrections = [],
  onCorrectionsChange,
  disabled = false,
}: DatasetReviewPanelProps) {
  const [editingCell, setEditingCell] = useState<string | null>(null);
  const [draftValue, setDraftValue] = useState("");
  const [editError, setEditError] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(1);

  const columnKeys =
    dataset.columns.length > 0
      ? dataset.columns.map((column) => column.key)
      : Object.keys(dataset.records[0]?.values ?? {});

  const totalRecords = dataset.records.length;
  const totalPages = Math.max(1, Math.ceil(totalRecords / PAGE_SIZE));
  const safeCurrentPage = Math.min(currentPage, totalPages);
  const pageStartIndex = (safeCurrentPage - 1) * PAGE_SIZE;
  const pageEndIndex = Math.min(pageStartIndex + PAGE_SIZE, totalRecords);
  const visibleRecords = dataset.records.slice(pageStartIndex, pageEndIndex);
  const editable = Boolean(onCorrectionsChange) && !disabled;

  useEffect(() => {
    setCurrentPage(1);
    setEditingCell(null);
    setDraftValue("");
    setEditError(null);
  }, [dataset.dataset_id]);

  useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  function goToPage(page: number) {
    const nextPage = Math.min(Math.max(page, 1), totalPages);
    setCurrentPage(nextPage);
    cancelEdit();
  }

  function findCorrection(recordIndex: number, columnKey: string) {
    return corrections.find(
      (item) => item.record_index === recordIndex && item.column_key === columnKey
    );
  }

  function beginEdit(recordIndex: number, columnKey: string, value: unknown) {
    if (!editable) return;
    setEditingCell(correctionKey(recordIndex, columnKey));
    setDraftValue(editableValue(value));
    setEditError(null);
  }

  function cancelEdit() {
    setEditingCell(null);
    setDraftValue("");
    setEditError(null);
  }

  function saveEdit(recordIndex: number, columnKey: string, originalValue: unknown) {
    if (!onCorrectionsChange) return;

    const column = dataset.columns.find((item) => item.key === columnKey);
    try {
      const correctedValue = parseEditedValue(draftValue, column?.data_type);
      const remaining = corrections.filter(
        (item) => !(item.record_index === recordIndex && item.column_key === columnKey)
      );

      if (sameValue(correctedValue, originalValue)) {
        onCorrectionsChange(remaining);
      } else {
        onCorrectionsChange([
          ...remaining,
          {
            record_index: recordIndex,
            column_key: columnKey,
            corrected_value: correctedValue,
            note: "使用者於人工資料確認頁修正",
          },
        ]);
      }

      cancelEdit();
    } catch (error) {
      setEditError(error instanceof Error ? error.message : "輸入格式不正確");
    }
  }

  function resetCorrection(recordIndex: number, columnKey: string) {
    if (!onCorrectionsChange) return;
    onCorrectionsChange(
      corrections.filter(
        (item) => !(item.record_index === recordIndex && item.column_key === columnKey)
      )
    );
    cancelEdit();
  }

  return (
    <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
      <div className="border-b border-gray-200 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold text-gray-900">{dataset.name}</h2>
              <span className="rounded-full bg-amber-50 px-2.5 py-1 text-xs font-semibold text-amber-700 ring-1 ring-amber-200">
                待人工確認
              </span>
            </div>
            <p className="mt-1 text-sm text-gray-500">
              {dataset.row_count} 列 × {dataset.column_count} 欄 · {dataset.source_kind}
            </p>
            {editable && (
              <p className="mt-2 text-xs font-medium text-gray-500">
                可點擊任一資料格人工修正；系統只會送出實際修改過的格子。
              </p>
            )}
          </div>

          <div className="text-right">
            <p className="text-xs text-gray-400">整體信心分數</p>
            <p
              className={`mt-1 text-xl font-bold ${
                dataset.confidence < lowConfidenceThreshold
                  ? "text-amber-600"
                  : "text-emerald-600"
              }`}
            >
              {confidenceLabel(dataset.confidence)}
            </p>
            {corrections.length > 0 && (
              <p className="mt-1 text-xs font-semibold text-emerald-600">
                已人工修改 {corrections.length} 格
              </p>
            )}
          </div>
        </div>

        {dataset.review_reasons.length > 0 && (
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-amber-700">
              需要確認的原因
            </p>
            <ul className="mt-2 space-y-1 text-sm text-amber-800">
              {dataset.review_reasons.map((reason) => (
                <li key={reason}>• {reason}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-sm">
          <thead>
            <tr className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
              <th className="sticky left-0 z-10 border-b border-r border-gray-200 bg-gray-50 px-3 py-3">
                #
              </th>
              {columnKeys.map((key) => {
                const column = dataset.columns.find((item) => item.key === key);
                return (
                  <th key={key} className="whitespace-nowrap border-b border-gray-200 px-4 py-3">
                    <span>{column?.label || key}</span>
                    {column?.unit && (
                      <span className="ml-1 font-normal normal-case text-gray-400">
                        ({column.unit})
                      </span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visibleRecords.map((record) => (
              <tr key={record.record_index} className="hover:bg-gray-50/70">
                <td className="sticky left-0 z-10 border-b border-r border-gray-100 bg-white px-3 py-3 text-xs text-gray-400">
                  {record.record_index + 1}
                </td>
                {columnKeys.map((key) => {
                  const dataValue = record.values[key];
                  const originalValue = dataValue?.value ?? dataValue?.raw_value;
                  const confidence = dataValue?.confidence ?? record.confidence;
                  const needsReview =
                    dataValue?.requires_human_review === true ||
                    record.requires_human_review ||
                    confidence < lowConfidenceThreshold;
                  const correction = findCorrection(record.record_index, key);
                  const shownValue = correction ? correction.corrected_value : originalValue;
                  const cellKey = correctionKey(record.record_index, key);
                  const isEditing = editingCell === cellKey;

                  return (
                    <td
                      key={cellKey}
                      className={`border-b border-gray-100 px-4 py-3 align-top ${
                        correction
                          ? "bg-emerald-50/80"
                          : needsReview
                            ? "bg-amber-50/80"
                            : ""
                      }`}
                    >
                      <div className="min-w-[140px]">
                        {isEditing ? (
                          <div>
                            <input
                              autoFocus
                              value={draftValue}
                              disabled={disabled}
                              onChange={(event) => {
                                setDraftValue(event.target.value);
                                setEditError(null);
                              }}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                  event.preventDefault();
                                  saveEdit(record.record_index, key, originalValue);
                                }
                                if (event.key === "Escape") {
                                  event.preventDefault();
                                  cancelEdit();
                                }
                              }}
                              className={`w-full rounded-md border bg-white px-2.5 py-2 text-sm font-medium text-gray-900 outline-none focus:ring-2 ${
                                editError
                                  ? "border-red-400 focus:border-red-500 focus:ring-red-500/15"
                                  : "border-red-300 focus:border-red-500 focus:ring-red-500/15"
                              }`}
                            />
                            {editError && (
                              <p className="mt-1 text-[11px] font-semibold text-red-600">
                                {editError}
                              </p>
                            )}
                            <div className="mt-2 flex gap-2">
                              <button
                                type="button"
                                onClick={() => saveEdit(record.record_index, key, originalValue)}
                                className="rounded-md bg-red-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-red-700"
                              >
                                套用
                              </button>
                              <button
                                type="button"
                                onClick={cancelEdit}
                                className="rounded-md border border-gray-200 bg-white px-2.5 py-1 text-[11px] font-semibold text-gray-600 hover:bg-gray-50"
                              >
                                取消
                              </button>
                            </div>
                          </div>
                        ) : (
                          <button
                            type="button"
                            disabled={!editable}
                            onClick={() => beginEdit(record.record_index, key, shownValue)}
                            className={`group w-full rounded-md text-left ${
                              editable ? "cursor-pointer hover:ring-2 hover:ring-red-500/15" : "cursor-default"
                            }`}
                            title={editable ? "點擊修改此資料格" : undefined}
                          >
                            <span className="flex items-start justify-between gap-2 px-1 py-0.5">
                              <span className={`font-medium ${correction ? "text-emerald-800" : "text-gray-800"}`}>
                                {displayValue(shownValue)}
                              </span>
                              {editable && (
                                <span className="text-xs text-gray-300 opacity-0 transition group-hover:opacity-100">
                                  ✎
                                </span>
                              )}
                            </span>
                          </button>
                        )}

                        {correction ? (
                          <div className="mt-1 flex items-center justify-between gap-2">
                            <p className="text-[11px] font-semibold text-emerald-600">
                              ✓ 已人工修改 · 原值 {displayValue(originalValue)}
                            </p>
                            <button
                              type="button"
                              disabled={disabled}
                              onClick={() => resetCorrection(record.record_index, key)}
                              className="text-[11px] font-semibold text-gray-400 hover:text-gray-700 disabled:cursor-not-allowed"
                            >
                              還原
                            </button>
                          </div>
                        ) : needsReview ? (
                          <p className="mt-1 text-[11px] font-semibold text-amber-600">
                            ⚠ {confidenceLabel(confidence)} confidence
                          </p>
                        ) : null}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {totalRecords > 0 && (
        <div className="flex flex-col gap-3 border-t border-gray-100 bg-gray-50 px-5 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-gray-500">
            顯示第 {pageStartIndex + 1}–{pageEndIndex} 筆，共 {totalRecords} 筆
          </p>

          {totalPages > 1 && (
            <div className="flex flex-wrap items-center justify-center gap-2 sm:justify-end">
              <button
                type="button"
                disabled={safeCurrentPage <= 1}
                onClick={() => goToPage(safeCurrentPage - 1)}
                className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                上一頁
              </button>

              <span className="min-w-[72px] text-center text-xs font-semibold text-gray-600">
                {safeCurrentPage} / {totalPages}
              </span>

              <button
                type="button"
                disabled={safeCurrentPage >= totalPages}
                onClick={() => goToPage(safeCurrentPage + 1)}
                className="rounded-md border border-gray-200 bg-white px-3 py-1.5 text-xs font-semibold text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40"
              >
                下一頁
              </button>

              <label className="ml-1 flex items-center gap-1.5 text-xs text-gray-500">
                跳至
                <select
                  value={safeCurrentPage}
                  disabled={disabled}
                  onChange={(event) => goToPage(Number(event.target.value))}
                  className="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-xs font-semibold text-gray-700 outline-none focus:border-red-400 focus:ring-2 focus:ring-red-500/10 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {Array.from({ length: totalPages }, (_, index) => index + 1).map((page) => (
                    <option key={page} value={page}>
                      第 {page} 頁
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
