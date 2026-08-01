const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const TOKEN_KEY = "auth_token";

function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const token = getToken();
  const headers = new Headers(options?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });

  if (res.status === 401) {
    // Token expired or invalid — clear auth state and redirect to login
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem("auth_user");
    window.location.href = "/login";
    throw new Error("登入已過期，請重新登入");
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.message || `HTTP ${res.status}`);
  }
  return res.json();
}
