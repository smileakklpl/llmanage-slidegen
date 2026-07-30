import { Link } from "react-router-dom";
import { useI18n } from "@/i18n";

export function Navbar() {
  const { locale, setLocale, t } = useI18n();

  function toggleLocale() {
    setLocale(locale === "zh" ? "en" : "zh");
  }

  return (
    <nav className="sticky top-0 z-50 bg-white border-b border-gray-200 shadow-sm">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-14">
          {/* Left: Logo + App name */}
          <Link to="/" className="flex items-center gap-3">
            {/* Logo placeholder — replace src with actual Taishin logo */}
            <img
              src="/logo.png"
              alt="Logo"
              className="h-8 w-auto object-contain"
              onError={(e) => {
                // Fallback if logo not found: hide image
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            <span className="text-lg font-bold text-gray-900">
              {t("appName")}
            </span>
          </Link>

          {/* Right: Language toggle */}
          <button
            onClick={toggleLocale}
            className="px-3 py-1.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
            aria-label="Toggle language"
          >
            {t("langToggle")}
          </button>
        </div>
      </div>
    </nav>
  );
}
