const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export async function apiFetch<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, options);
  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.message || `HTTP ${res.status}`);
  }
  return res.json();
}
