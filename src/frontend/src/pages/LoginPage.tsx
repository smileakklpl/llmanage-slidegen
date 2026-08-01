import { useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useAuth } from "@/auth";
import { loginApi } from "@/auth/authApi";
import { useI18n } from "@/i18n";

export function LoginPage() {
  const { t } = useI18n();
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    const trimmed = email.trim();
    if (!trimmed) {
      setError(t("loginEmailRequired"));
      return;
    }

    setIsLoading(true);
    try {
      const res = await loginApi(trimmed);
      login(res.access_token, { email: res.email, name: res.name });
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("unknownError"));
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="w-full max-w-sm">
        <div className="bg-white rounded-2xl shadow-lg border border-gray-200 p-8">
          {/* Header */}
          <div className="text-center mb-8">
            <img
              src="/logo.png"
              alt="Logo"
              className="h-10 w-auto mx-auto mb-3 object-contain"
              onError={(e) => {
                (e.target as HTMLImageElement).style.display = "none";
              }}
            />
            <h1 className="text-xl font-bold text-gray-900">{t("appName")}</h1>
            <p className="text-sm text-gray-500 mt-1">{t("loginSubtitle")}</p>
          </div>

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-2">
              <label
                htmlFor="login-email"
                className="block text-xs font-semibold text-gray-500 uppercase tracking-wide"
              >
                Email
              </label>
              <input
                id="login-email"
                type="email"
                autoComplete="email"
                autoFocus
                placeholder={t("loginEmailPlaceholder")}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isLoading}
                className="w-full rounded-lg border border-gray-300 px-3 py-2.5 text-sm placeholder:text-gray-400 focus:border-red-500 focus:ring-2 focus:ring-red-500/20 focus:outline-none disabled:opacity-50 transition-all"
              />
            </div>

            {error && (
              <p className="text-sm text-red-600 bg-red-50 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={isLoading}
              className="w-full py-2.5 rounded-lg bg-red-600 text-white text-sm font-semibold hover:bg-red-700 focus:ring-2 focus:ring-red-500/50 focus:outline-none disabled:opacity-50 transition-colors"
            >
              {isLoading ? t("loginLoading") : t("loginButton")}
            </button>
          </form>

          {/* Register link */}
          <p className="text-center text-sm text-gray-500 mt-6">
            {t("loginNoAccount")}{" "}
            <Link
              to="/register"
              className="text-red-600 font-medium hover:text-red-700"
            >
              {t("loginRegisterLink")}
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
