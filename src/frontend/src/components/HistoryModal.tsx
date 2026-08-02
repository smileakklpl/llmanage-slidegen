import { useEffect, useState } from "react";
import { useI18n } from "@/i18n";
import { apiFetch } from "@/api/client";

interface ArtifactItem {
  filename: string;
  object_key: string;
  type: string;
  size_bytes: number;
}

interface HistoryRecord {
  record_id: string;
  job_id: string;
  prompt: string;
  created_at: string;
  artifacts: ArtifactItem[];
}

interface HistoryListResponse {
  records: HistoryRecord[];
  count: number;
}

interface DownloadUrlResponse {
  url: string;
  filename: string;
}

interface HistoryModalProps {
  onClose: () => void;
}

export function HistoryModal({ onClose }: HistoryModalProps) {
  const { t } = useI18n();
  const [records, setRecords] = useState<HistoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  useEffect(() => {
    fetchHistory();
  }, []);

  async function fetchHistory() {
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<HistoryListResponse>("/auth/history");
      setRecords(data.records);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("historyError"));
    } finally {
      setLoading(false);
    }
  }

  async function handleDownload(recordId: string, filename: string) {
    try {
      const data = await apiFetch<DownloadUrlResponse>(
        `/auth/history/${encodeURIComponent(recordId)}/download/${encodeURIComponent(filename)}`
      );
      window.open(data.url, "_blank");
    } catch (err) {
      alert(err instanceof Error ? err.message : t("historyError"));
    }
  }

  async function handleDelete(recordId: string) {
    if (!confirm(t("historyDeleteConfirm"))) return;

    setDeletingId(recordId);
    try {
      await apiFetch(`/auth/history/${encodeURIComponent(recordId)}`, {
        method: "DELETE",
      });
      setRecords((prev) => prev.filter((r) => r.record_id !== recordId));
    } catch (err) {
      alert(err instanceof Error ? err.message : t("historyError"));
    } finally {
      setDeletingId(null);
    }
  }

  function formatDate(iso: string) {
    try {
      return new Date(iso).toLocaleString();
    } catch {
      return iso;
    }
  }

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="bg-white rounded-2xl shadow-2xl border border-gray-200 w-full max-w-2xl max-h-[80vh] flex flex-col mx-4">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h2 className="text-lg font-bold text-gray-900">
            {t("historyTitle")}
          </h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 transition-colors"
            aria-label={t("historyClose")}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {loading && (
            <p className="text-center text-gray-500 py-8">{t("historyLoading")}</p>
          )}

          {error && (
            <p className="text-center text-red-600 py-8">{error}</p>
          )}

          {!loading && !error && records.length === 0 && (
            <p className="text-center text-gray-500 py-8">{t("historyEmpty")}</p>
          )}

          {!loading && !error && records.length > 0 && (
            <div className="space-y-4">
              {records.map((record) => (
                <div
                  key={record.record_id}
                  className="border border-gray-200 rounded-xl p-4 hover:border-gray-300 transition-colors"
                >
                  {/* Record header */}
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {record.prompt}
                      </p>
                      <p className="text-xs text-gray-500 mt-0.5">
                        {formatDate(record.created_at)}
                      </p>
                    </div>
                    <button
                      onClick={() => handleDelete(record.record_id)}
                      disabled={deletingId === record.record_id}
                      className="text-xs text-red-500 hover:text-red-700 font-medium px-2 py-1 rounded hover:bg-red-50 transition-colors disabled:opacity-50 flex-shrink-0"
                    >
                      {t("historyDelete")}
                    </button>
                  </div>

                  {/* Artifacts */}
                  {record.artifacts.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {record.artifacts.map((artifact) => (
                        <div
                          key={artifact.filename}
                          className="flex items-center justify-between gap-2 text-sm bg-gray-50 rounded-lg px-3 py-2"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <svg className="w-4 h-4 text-gray-400 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z" />
                            </svg>
                            <span className="text-gray-700 truncate">
                              {artifact.filename}
                            </span>
                          </div>
                          <button
                            onClick={() => handleDownload(record.record_id, artifact.filename)}
                            className="text-xs text-blue-600 hover:text-blue-800 font-medium px-2 py-0.5 rounded hover:bg-blue-50 transition-colors flex-shrink-0"
                          >
                            {t("historyDownload")}
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-gray-100">
          <button
            onClick={onClose}
            className="w-full py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
          >
            {t("historyClose")}
          </button>
        </div>
      </div>
    </div>
  );
}
