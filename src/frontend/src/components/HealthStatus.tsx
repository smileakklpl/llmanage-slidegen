import { useQuery } from "@tanstack/react-query";
import { fetchHealth } from "@/api/healthApi";
import { useI18n } from "@/i18n";

export function HealthStatus() {
  const { t } = useI18n();
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 30000,
  });

  // Only show when there's an error
  if (isError || (data && data.status !== "ok")) {
    return (
      <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm font-medium">
        <span className="w-2 h-2 rounded-full bg-red-500" />
        {t("healthError")}
      </div>
    );
  }

  // Normal or loading — don't show anything
  return null;
}
