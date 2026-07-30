import { apiFetch } from "@/api/client";
import {
  jobCreateResponseSchema,
  jobStatusResponseSchema,
} from "@/schemas/jobSchema";
import type { JobCreateResponse, JobStatusResponse } from "@/types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

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
    body: formData,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.message || `HTTP ${res.status}`);
  }

  const data = await res.json();
  return jobCreateResponseSchema.parse(data);
}

export async function getJobStatus(jobId: string): Promise<JobStatusResponse> {
  const data = await apiFetch<unknown>(`/api/v1/jobs/${jobId}`);
  return jobStatusResponseSchema.parse(data);
}

export interface SendEmailRequest {
  sender: string;
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

  formData.append("sender", payload.sender);
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
    body: formData,
  });

  if (!res.ok) {
    const error = await res.json().catch(() => ({ message: "未知錯誤" }));
    throw new Error(error.message || `HTTP ${res.status}`);
  }

  return res.json();
}
