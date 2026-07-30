import { apiFetch } from "@/api/client";
import { healthResponseSchema } from "@/schemas/healthSchema";
import type { HealthResponse } from "@/types";

export async function fetchHealth(): Promise<HealthResponse> {
  const data = await apiFetch<unknown>("/api/v1/health");
  return healthResponseSchema.parse(data);
}
