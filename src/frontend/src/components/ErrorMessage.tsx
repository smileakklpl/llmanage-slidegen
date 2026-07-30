import { useI18n } from "@/i18n";

interface ErrorMessageProps {
  message: string;
  onRetry?: () => void;
}

export function ErrorMessage({ message, onRetry }: ErrorMessageProps) {
  const { t } = useI18n();

  return (
    <div className="p-4 bg-red-50 border border-red-200 rounded-xl space-y-3">
      <p className="text-sm text-red-800 font-medium">{message}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-4 py-2 rounded-lg border border-gray-300 bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
        >
          {t("retryButton")}
        </button>
      )}
    </div>
  );
}
