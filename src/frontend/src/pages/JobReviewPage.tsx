import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getJobReview, reviewJobDataset, resumeJob } from "@/api/jobsApi";
import { DatasetReviewPanel } from "@/components/review/DatasetReviewPanel";
import { ErrorMessage } from "@/components/ErrorMessage";
import type { DatasetCorrection } from "@/schemas/ingestionSchema";

export function JobReviewPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const {
    data: reviewData,
    error: fetchError,
    refetch,
  } = useQuery({
    queryKey: ["job-review", jobId],
    queryFn: () => getJobReview(jobId!),
    enabled: !!jobId,
  });

  const [reviewer, setReviewer] = useState("user");
  const [notes, setNotes] = useState("");
  const [corrections, setCorrections] = useState<DatasetCorrection[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isResuming, setIsResuming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (fetchError) {
    return (
      <div className="mx-auto max-w-xl px-6 py-16">
        <ErrorMessage
          message={fetchError instanceof Error ? fetchError.message : "無法載入審查資料"}
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  if (!reviewData) {
    return (
      <div className="mx-auto max-w-xl px-6 py-16 text-center">
        <div className="flex items-center justify-center gap-3 text-gray-500">
          <div className="w-5 h-5 border-2 border-gray-300 border-t-red-600 rounded-full animate-spin" />
          <span>載入審查資料中…</span>
        </div>
      </div>
    );
  }

  const datasets = reviewData.datasets;
  const pendingDatasets = datasets.filter((d) => {
    const status = (d as Record<string, unknown>).review_status;
    return status !== "approved" && status !== "rejected";
  });
  const approvedCount = datasets.filter(
    (d) => (d as Record<string, unknown>).review_status === "approved"
  ).length;
  const rejectedCount = datasets.filter(
    (d) => (d as Record<string, unknown>).review_status === "rejected"
  ).length;
  const reviewedCount = approvedCount + rejectedCount;
  const lowConfidenceCount = datasets.filter(
    (d) => (d as Record<string, unknown>).requires_human_review === true
  ).length;
  const currentDataset = pendingDatasets[0] ?? null;

  async function handleReview(decision: "approve" | "reject") {
    if (!currentDataset || !jobId) return;
    if (!reviewer.trim()) {
      setError("請填寫確認者名稱");
      return;
    }

    const datasetId = (currentDataset as Record<string, unknown>).dataset_id as string;

    setIsSubmitting(true);
    setError(null);
    try {
      await reviewJobDataset(jobId, datasetId, {
        decision,
        reviewer: reviewer.trim(),
        notes: notes.trim() || undefined,
        corrections: decision === "approve" ? corrections : [],
      });
      setNotes("");
      setCorrections([]);
      // Refresh the review data
      await refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "人工確認失敗");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleResume() {
    if (!jobId) return;

    setIsResuming(true);
    setError(null);
    try {
      await resumeJob(jobId);
      // Invalidate job status cache so JobPage picks up the new state
      queryClient.invalidateQueries({ queryKey: ["job", jobId] });
      navigate(`/jobs/${jobId}`, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "繼續生成失敗");
    } finally {
      setIsResuming(false);
    }
  }

  // All datasets reviewed — show resume button
  if (!currentDataset) {
    const hasRejected = datasets.some(
      (d) => (d as Record<string, unknown>).review_status === "rejected"
    );

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
              : `已完成 ${approvedCount} 個資料集的確認，可以繼續生成簡報。`}
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
                disabled={isResuming}
                onClick={handleResume}
                className="rounded-lg bg-red-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-300"
              >
                {isResuming ? "啟動生成中…" : "確認完成，繼續生成簡報"}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  const currentPosition = reviewedCount + 1;
  const totalCount = datasets.length;

  // Cast for DatasetReviewPanel compatibility
  const panelDataset = currentDataset as unknown as import("@/schemas/ingestionSchema").UnifiedDatasetSpec;

  return (
    <div className="mx-auto max-w-[1500px] px-5 py-8 lg:px-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <button
            type="button"
            onClick={() => navigate(`/jobs/${jobId}`)}
            className="mb-3 text-sm font-medium text-gray-500 hover:text-gray-800"
          >
            ← 返回工作狀態
          </button>
          <h1 className="text-2xl font-bold text-gray-900">人工資料確認</h1>
          <p className="mt-1 text-sm text-gray-500">
            生成前請確認所有抽取資料；任何資料格都可以手動修正。
            {lowConfidenceCount > 0 && (
              <span className="ml-1 font-medium text-amber-700">
                其中 {lowConfidenceCount} 個資料集信心較低，請特別核對黃色標示。
              </span>
            )}
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
              style={{ width: `${Math.round((reviewedCount / totalCount) * 100)}%` }}
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
        {/* Source preview placeholder — job-based flow uses S3 presigned URLs */}
        <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <h2 className="text-sm font-semibold text-gray-700 mb-3">原始資料來源</h2>
          {reviewData.sources.length > 0 ? (
            <ul className="space-y-2 text-sm text-gray-600">
              {reviewData.sources.map((source) => (
                <li key={source.filename} className="flex items-center gap-2">
                  <span className="text-gray-400">📄</span>
                  <a
                    href={source.preview_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-red-600 hover:underline"
                  >
                    {source.filename}
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-400">無可預覽來源檔</p>
          )}
        </div>

        <div className="space-y-5">
          <DatasetReviewPanel
            dataset={panelDataset}
            corrections={corrections}
            onCorrectionsChange={setCorrections}
            disabled={isSubmitting}
          />

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
                {corrections.length > 0
                  ? `已修改 ${corrections.length} 格。確認後會先套用人工修正，再讓資料進入後續生成流程。`
                  : "確認表示你已核對此資料集；信心較低的欄位會以黃色標示，但所有欄位都可以修改。"}
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
                  {isSubmitting
                    ? "送出中…"
                    : corrections.length > 0
                      ? `確認並套用 ${corrections.length} 項修正`
                      : "確認資料正確"}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
