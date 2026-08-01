/**
 * Auth API calls to backend /auth/* endpoints.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export interface AuthResponse {
  access_token: string;
  token_type: string;
  email: string;
  name: string;
}

export async function loginApi(email: string): Promise<AuthResponse> {
  const res = await fetch(`${BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "未知錯誤" }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }

  return res.json();
}

export async function registerApi(
  email: string,
  name: string
): Promise<AuthResponse> {
  const res = await fetch(`${BASE_URL}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, name }),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "未知錯誤" }));
    throw new Error(error.detail || `HTTP ${res.status}`);
  }

  return res.json();
}
