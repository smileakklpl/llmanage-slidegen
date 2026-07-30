import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { HealthStatus } from "@/components/HealthStatus";
import { GenerateForm } from "@/components/GenerateForm";
import { ErrorMessage } from "@/components/ErrorMessage";
import { generateJob } from "@/api/jobsApi";
import { useI18n } from "@/i18n";

export function GeneratePage() {
  const navigate = useNavigate();
  const { t } = useI18n();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(files: File[], prompt: string) {
    setIsSubmitting(true);
    setError(null);
    try {
      const result = await generateJob(files, prompt);
      navigate(`/jobs/${result.job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("unknownError"));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto px-6 py-10">
      <div className="text-center mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">
          {t("heroTitle")}
        </h1>
        <p className="text-gray-500 text-sm">
          {t("heroSubtitle")}
        </p>
      </div>

      <div className="mb-6">
        <HealthStatus />
      </div>

      {error && (
        <div className="mb-6">
          <ErrorMessage message={error} onRetry={() => setError(null)} />
        </div>
      )}

      <div className="bg-white border border-gray-200 rounded-2xl shadow-sm p-8">
        <GenerateForm onSubmit={handleSubmit} isSubmitting={isSubmitting} />
      </div>
    </div>
  );
}
