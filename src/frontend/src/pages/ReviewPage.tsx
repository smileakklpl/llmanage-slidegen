import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { generateJob } from "@/api/jobsApi";
import { reviewDataset } from "@/api/ingestionApi";
import { DatasetReviewPanel } from "@/components/review/DatasetReviewPanel";
import { SourcePreview } from "@/components/review/SourcePreview";
import type {
  UnifiedDatasetSpec,
  UnifiedIngestionResult,
} from "@/schemas/ingestionSchema";

interface ReviewRouteState {
  files: File[];
  prompt: string;
  ingestionResults: UnifiedIngestionResult[];
}

function isReviewRouteState(value: unknown): value is ReviewRouteState {
  if (!value || typeof value !== "object") return false;
  const state = value as Partial<ReviewRouteState>;
  return (
    Array.isArray(state.files) &&
    typeof state.prompt === "string" &&
    Array.isArray(state.ingestionResults)
  );
}

export function ReviewPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const routeState = isReviewRouteState(location.state) ? location.state : null;

  const initialDatasets = useMemo(
    () =>
      routeState?.ingestionResults.flatMap((result) =>
        result.datasets.filter(
          (dataset) => dataset.requires_human_review || dataset.review_status === "pending"
        )
      ) ?? [],
    [routeState]
  );

  const [datasets, setDatasets] = useState<UnifiedDatasetSpec[]>(initialDatasets);
  const [reviewer, setReviewer] = useState("user");
  const [notes, setNotes] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pendingDatasets = datasets.filter(
    (dataset) => dataset.review_status !== "approved" && dataset.review_status !== "rejected"
  );
  const rejectedDatasets = datasets.filter((dataset) => dataset.review_status === "rejected");
  const approvedCount = datasets.filter((dataset) => dataset.review_status === "approved").length;
  const currentDataset = pendingDatasets[0] ?? null;

  const sourceFile = routeState?.files.find(
    (file) => file.name === currentDataset?.filename
  );

  if (!routeState) {
    return (
      <div className="mx-auto max-w-xl px-6 py-16 text-center">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
          <div className="text-4xl">⚠️</div>
          <h1 className="mt-4 text-xl font-bold text-gray-900">找不到待確認資料</h1>
          <p className="mt-2 text-sm leading-6 text-gray-500">
            此頁面需要從檔案上傳流程進入。重新整理頁面後，瀏覽器不會保留原始上傳檔案。
          </p>
          <button
            type="button"
            onClick={() => navigate("/")}
            className="mt-6 rounded-lg bg-red-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-red-700"
          >
            回到上傳頁
          </button>
        </div>
      </div>
    );
  }

  // routeState 已通過上方 guard，因此後續事件處理函式可安全使用。
  // 使用獨立常數也能避免 TypeScript 在 async closure 中重新判定為 null。
  const confirmedRouteState = routeState;

  function replaceDataset(updated: UnifiedDatasetSpec) {
    setDatasets((current) =>
      current.map((dataset) =>
        dataset.dataset_id === updated.dataset_id ? updated : dataset
      )
    );
  }

  async function handleReview(decision: "approve" | "reject") {
    if (!currentDataset) return;
    if (!reviewer.trim()) {
      setError("請填寫確認者名稱");
      return;
    }

    setIsSubmitting(true);
    setError(null);
    try {
      const updated = await reviewDataset(currentDataset, {
        decision,
        reviewer: reviewer.trim(),
        notes: notes.trim() || undefined,
        corrections: [],
      });
      replaceDataset(updated);
      setNotes("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "人工確認失敗");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleGenerate() {
    if (rejectedDatasets.length > 0 || pendingDatasets.length > 0) return;

    setIsGenerating(true);
    setError(null);
    try {
      const result = await generateJob(
        confirmedRouteState.files,
        confirmedRouteState.prompt
      );
      navigate(`/jobs/${result.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "建立簡報工作失敗");
    } finally {
      setIsGenerating(false);
    }
  }

  if (!currentDataset) {
    const hasRejected = rejectedDatasets.length > 0;

    return (
      <div className="mx-auto max-w-2xl px-6 py-12">
        <div className="rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm">
          <div
            className={`mx-auto flex h-16 w-16 items-center justify-center rounded-full text-3xl ${
              hasRejected ? "bg-red-50" : "bg-emerald-50"
            }`}
          >
            {hasRejected ? "✕" : "✓"}
          </div>
          <h1 className="mt-5 text-2xl font-bold text-gray-900">
            {hasRejected ? "有資料集被拒絕" : "人工確認完成"}
          </h1>
          <p className="mt-2 text-sm leading-6 text-gray-500">
            {hasRejected
              ? "被拒絕的資料不應進入分析。請回到上傳頁更換或修正來源檔案。"
              : `已完成 ${approvedCount} 個低信心資料集的人工確認，可以開始產生簡報。`}
          </p>

          {error && (
            <div className="mt-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              {error}
            </div>
          )}

          <div className="mt-7 flex justify-center gap-3">
            <button
              type="button"
              onClick={() => navigate("/")}
              className="rounded-lg border border-gray-300 bg-white px-5 py-2.5 text-sm font-semibold text-gray-700 hover:bg-gray-50"
            >
              {hasRejected ? "重新上傳" : "返回首頁"}
            </button>
            {!hasRejected && (
              <button
                type="button"
                disabled={isGenerating}
                onClick={handleGenerate}
                className="rounded-lg bg-red-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-300"
              >
                {isGenerating ? "建立工作中…" : "確認完成，開始生成簡報"}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  const currentPosition = approvedCount + 1;
  const totalCount = datasets.length;

  return (
    <div className="mx-auto max-w-[1500px] px-5 py-8 lg:px-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <button
            type="button"
            onClick={() => navigate("/")}
            className="mb-3 text-sm font-medium text-gray-500 hover:text-gray-800"
          >
            ← 返回上傳
          </button>
          <h1 className="text-2xl font-bold text-gray-900">人工資料確認</h1>
          <p className="mt-1 text-sm text-gray-500">
            系統偵測到低信心資料。請比對原始文件與抽取結果後再決定是否使用。
          </p>
        </div>

        <div className="min-w-[220px] rounded-xl border border-gray-200 bg-white px-4 py-3 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>確認進度</span>
            <span>{currentPosition} / {totalCount}</span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-gray-100">
            <div
              className="h-full rounded-full bg-red-600 transition-all"
              style={{ width: `${Math.round((approvedCount / totalCount) * 100)}%` }}
            />
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid gap-5 xl:grid-cols-[0.85fr_1.35fr]">
        <SourcePreview file={sourceFile} filename={currentDataset.filename} />

        <div className="space-y-5">
          <DatasetReviewPanel dataset={currentDataset} />

          <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label className="block text-xs font-semibold text-gray-600">
                  確認者
                </label>
                <input
                  value={reviewer}
                  onChange={(event) => setReviewer(event.target.value)}
                  className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-red-500 focus:ring-2 focus:ring-red-500/15"
                  placeholder="例如：Brian"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600">
                  備註（選填）
                </label>
                <input
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  className="mt-2 w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm outline-none transition focus:border-red-500 focus:ring-2 focus:ring-red-500/15"
                  placeholder="例如：已與原始 PDF 核對"
                />
              </div>
            </div>

            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 pt-5">
              <p className="text-xs leading-5 text-gray-500">
                Approve 表示你已核對原始文件；Reject 會阻止這批資料進入後續生成流程。
              </p>
              <div className="flex gap-3">
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => handleReview("reject")}
                  className="rounded-lg border border-red-200 bg-white px-5 py-2.5 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  拒絕此資料
                </button>
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => handleReview("approve")}
                  className="rounded-lg bg-red-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-300"
                >
                  {isSubmitting ? "送出中…" : "確認資料正確"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
