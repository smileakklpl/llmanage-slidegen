import { apiFetch } from "@/api/client";
import {
  jobCreateResponseSchema,
  jobStatusResponseSchema,
} from "@/schemas/jobSchema";
import type { JobCreateResponse, JobStatusResponse } from "@/types";
import type { HumanReviewRequest } from "@/schemas/ingestionSchema";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
const TOKEN_KEY = "auth_token";

function getAuthHeaders(): HeadersInit {
  const token = localStorage.getItem(TOKEN_KEY);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface JobReviewResponse {
  job_id: string;
  review_required_count: number;
  can_resume: boolean;
  datasets: Record<string, unknown>[];
  sources: { filename: string; preview_url: string }[];
}

export interface ResumeJobResponse {
  job_id: string;
  status: string;
  stage: string;
  message: string;
}

export async function generateJob(
  files: File[],
  prompt: string
): Promise<JobCreateResponse> {
  const formData = new FormData();
  for (const file of files) {
    formData.append("files", file);
  }
  formData.append("prompt", prompt);

  const res = await fetch(`${BASE_URL}/api/v1/jobs/generate`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.detail || error.message || `HTTP ${res.status}`);
  }

  const data = await res.json();
  return jobCreateResponseSchema.parse(data);
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const data = await apiFetch<unknown>(`/api/v1/jobs/${jobId}`);
  return jobStatusResponseSchema.parse(data);
}

export async function getJobReview(jobId: string): Promise<JobReviewResponse> {
  return apiFetch<JobReviewResponse>(`/api/v1/jobs/${jobId}/review`);
}

export async function reviewJobDataset(
  jobId: string,
  datasetId: string,
  review: HumanReviewRequest
): Promise<Record<string, unknown>> {
  const res = await fetch(`${BASE_URL}/api/v1/jobs/${jobId}/datasets/${datasetId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(review),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.detail || error.message || `HTTP ${res.status}`);
  }

  return res.json();
}

export async function resumeJob(jobId: string): Promise<ResumeJobResponse> {
  const res = await fetch(`${BASE_URL}/api/v1/jobs/${jobId}/resume`, {
    method: "POST",
    headers: getAuthHeaders(),
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.detail || error.message || `HTTP ${res.status}`);
  }

  return res.json();
}

export interface SendEmailRequest {
  recipients: string[];
  subject: string;
  body: string;
  attachments: File[];
  artifactFilenames: string[];
}

export interface SendEmailResponse {
  job_id: string;
  recipients: string[];
  message: string;
}

export async function sendJobEmail(
  jobId: string,
  payload: SendEmailRequest
): Promise<SendEmailResponse> {
  const formData = new FormData();

  for (const recipient of payload.recipients) {
    formData.append("recipients", recipient);
  }
  formData.append("subject", payload.subject);
  formData.append("body", payload.body);
  for (const filename of payload.artifactFilenames) {
    formData.append("artifact_filenames", filename);
  }
  for (const file of payload.attachments) {
    formData.append("attachments", file);
  }

  const res = await fetch(`${BASE_URL}/api/v1/jobs/${jobId}/send`, {
    method: "POST",
    headers: getAuthHeaders(),
    body: formData,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.message || `HTTP ${res.status}`);
  }

  return res.json();
}
