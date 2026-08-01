import {
  unifiedDatasetSpecSchema,
  unifiedIngestionResultSchema,
  type HumanReviewRequest,
  type UnifiedDatasetSpec,
  type UnifiedIngestionResult,
} from "@/schemas/ingestionSchema";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

// The current backend mounts a router that already has /ingestion under
// another /ingestion prefix, so the deployed path is /ingestion/ingestion.
// Override this with VITE_INGESTION_BASE_PATH after the backend prefix is cleaned up.
const INGESTION_BASE_PATH =
  import.meta.env.VITE_INGESTION_BASE_PATH ?? "/ingestion/ingestion";

async function readApiError(res: Response): Promise<string> {
  const payload = await res.json().catch(() => null);
  if (payload && typeof payload === "object") {
    const detail = "detail" in payload ? payload.detail : undefined;
    const message = "message" in payload ? payload.message : undefined;

    if (typeof detail === "string") return detail;
    if (typeof message === "string") return message;
  }
  return `HTTP ${res.status}`;
}

export async function processUploadedFile(file: File): Promise<UnifiedIngestionResult> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${BASE_URL}${INGESTION_BASE_PATH}/process`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    throw new Error(await readApiError(res));
  }

  return unifiedIngestionResultSchema.parse(await res.json());
}

export async function reviewDataset(
  dataset: UnifiedDatasetSpec,
  review: HumanReviewRequest
): Promise<UnifiedDatasetSpec> {
  const res = await fetch(`${BASE_URL}${INGESTION_BASE_PATH}/review-dataset`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ dataset, review }),
  });

  if (!res.ok) {
    throw new Error(await readApiError(res));
  }

  return unifiedDatasetSpecSchema.parse(await res.json());
}
