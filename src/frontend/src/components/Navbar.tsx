import { useState, useRef, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/auth";
import { useI18n } from "@/i18n";

export function Navbar() {
  const { locale, setLocale, t } = useI18n();
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  function toggleLocale() {
    setLocale(locale === "zh" ? "en" : "zh");
  }

  function handleLogout() {
    logout();
    setMenuOpen(false);
    navigate("/login", { replace: true });
  }

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    if (menuOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [menuOpen]);

  return (
    <nav className="sticky top-0 z-50 bg-white border-b border-gray-200 shadow-sm">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-14">
          {/* Left: Logo + App name */}
          <Link to="/" className="flex items-center gap-3">
            <img
              src="/logo.png"
              alt="Logo"
              className="h-8 w-auto object-contain"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            <span className="text-lg font-bold text-gray-900">
              {t("appName")}
            </span>
          </Link>

          {/* Right: User menu + Language toggle */}
          <div className="flex items-center gap-3">
            {/* User dropdown */}
            {user && (
              <div className="relative" ref={menuRef}>
                <button
                  onClick={() => setMenuOpen((prev) => !prev)}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors max-w-[200px]"
                  aria-haspopup="true"
                  aria-expanded={menuOpen}
                >
                  {/* User avatar circle */}
                  <span className="w-6 h-6 rounded-full bg-red-100 text-red-700 flex items-center justify-center text-xs font-bold flex-shrink-0">
                    {user.name.charAt(0).toUpperCase()}
                  </span>
                  <span className="truncate">{user.email}</span>
                  {/* Chevron */}
                  <svg
                    className={`w-4 h-4 flex-shrink-0 transition-transform ${menuOpen ? "rotate-180" : ""}`}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M19 9l-7 7-7-7"
                    />
                  </svg>
                </button>

                {/* Dropdown menu */}
                {menuOpen && (
                  <div className="absolute right-0 mt-2 w-48 bg-white border border-gray-200 rounded-xl shadow-lg py-1 z-50">
                    <div className="px-4 py-2 border-b border-gray-100">
                      <p className="text-sm font-medium text-gray-900 truncate">
                        {user.name}
                      </p>
                      <p className="text-xs text-gray-500 truncate">
                        {user.email}
                      </p>
                    </div>
                    <button
                      onClick={handleLogout}
                      className="w-full text-left px-4 py-2 text-sm text-red-600 hover:bg-red-50 transition-colors"
                    >
                      {t("userMenuLogout")}
                    </button>
                  </div>
                )}
              </div>
            )}

            <button
              onClick={toggleLocale}
              className="px-3 py-1.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 transition-colors"
              aria-label="Toggle language"
            >
              {t("langToggle")}
            </button>
          </div>
        </div>
      </div>
    </nav>
  );
}
