/**
 * Zod schemas for job-related API responses.
 * These schemas match contracts/job-status.schema.json.
 */

import { z } from "zod";

/** High-level status of a job. */
export const jobStatusEnum = z.enum(["queued", "running", "waiting_review", "succeeded", "failed"]);

/** Processing stage of a job. */
export const jobStageEnum = z.enum([
  "queued",
  "parsing_intent",
  "analyzing_data",
  "reviewing_data",
  "writing_insights",
  "rendering",
  "validating",
  "completed",
  "failed",
]);

/** An output artifact produced by a completed job. */
export const artifactSchema = z.object({
  type: z.string(),
  filename: z.string(),
  download_url: z.string(),
});

/** Error details when a job fails. */
export const jobErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
});

/** Full job status response (GET /api/v1/jobs/{job_id}). */
export const jobStatusResponseSchema = z.object({
  job_id: z.string(),
  status: jobStatusEnum,
  stage: jobStageEnum,
  progress: z.number().int().min(0).max(100),
  message: z.string(),
  created_at: z.string(),
  updated_at: z.string(),
  artifacts: z.array(artifactSchema),
  error: jobErrorSchema.nullable(),
  summary: z.string().nullable().optional(),
  review_required_count: z.number().int().nonnegative().default(0),
  review_url: z.string().nullable().optional(),
});

/** Response for POST /api/v1/jobs/generate (HTTP 202). */
export const jobCreateResponseSchema = z.object({
  job_id: z.string(),
  status: z.string(),
  status_url: z.string(),
});

// Inferred TypeScript types
export type JobStatus = z.infer<typeof jobStatusEnum>;
export type JobStage = z.infer<typeof jobStageEnum>;
export type Artifact = z.infer<typeof artifactSchema>;
export type JobError = z.infer<typeof jobErrorSchema>;
export type JobStatusResponse = z.infer<typeof jobStatusResponseSchema>;
export type JobCreateResponse = z.infer<typeof jobCreateResponseSchema>;
