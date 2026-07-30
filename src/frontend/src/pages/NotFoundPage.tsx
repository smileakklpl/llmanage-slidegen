import { Link } from "react-router-dom";
import { useI18n } from "@/i18n";

export function NotFoundPage() {
  const { t } = useI18n();

  return (
    <div className="max-w-2xl mx-auto px-6 py-20 text-center">
      <h1 className="text-6xl font-bold text-gray-200 mb-4">
        {t("notFoundTitle")}
      </h1>
      <p className="text-lg text-gray-600 mb-8">{t("notFoundMessage")}</p>
      <Link
        to="/"
        className="inline-block px-6 py-3 rounded-lg bg-red-600 text-white font-semibold shadow hover:bg-red-700 hover:shadow-md transition-all duration-200"
      >
        {t("notFoundBack")}
      </Link>
    </div>
  );
}
