import type { UnifiedDatasetSpec } from "@/schemas/ingestionSchema";

interface DatasetReviewPanelProps {
  dataset: UnifiedDatasetSpec;
  lowConfidenceThreshold?: number;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function confidenceLabel(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

export function DatasetReviewPanel({
  dataset,
  lowConfidenceThreshold = 0.85,
}: DatasetReviewPanelProps) {
  const columnKeys =
    dataset.columns.length > 0
      ? dataset.columns.map((column) => column.key)
      : Object.keys(dataset.records[0]?.values ?? {});

  const visibleRecords = dataset.records.slice(0, 30);
  const hiddenCount = Math.max(0, dataset.records.length - visibleRecords.length);

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
                  const confidence = dataValue?.confidence ?? record.confidence;
                  const needsReview =
                    dataValue?.requires_human_review === true ||
                    record.requires_human_review ||
                    confidence < lowConfidenceThreshold;

                  return (
                    <td
                      key={`${record.record_index}-${key}`}
                      className={`border-b border-gray-100 px-4 py-3 align-top ${
                        needsReview ? "bg-amber-50/80" : ""
                      }`}
                    >
                      <div className="min-w-[110px]">
                        <p className="font-medium text-gray-800">
                          {displayValue(dataValue?.value ?? dataValue?.raw_value)}
                        </p>
                        {needsReview && (
                          <p className="mt-1 text-[11px] font-semibold text-amber-600">
                            ⚠ {confidenceLabel(confidence)} confidence
                          </p>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hiddenCount > 0 && (
        <div className="border-t border-gray-100 bg-gray-50 px-5 py-3 text-center text-xs text-gray-500">
          為避免畫面過長，目前顯示前 30 列，另有 {hiddenCount} 列未顯示。
        </div>
      )}
    </div>
  );
}
