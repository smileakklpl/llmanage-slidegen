import { z } from "zod";

export const unifiedDataValueSchema = z
  .object({
    raw_value: z.unknown().nullable().optional(),
    value: z.unknown().nullable().optional(),
    confidence: z.number().min(0).max(1),
    requires_human_review: z.boolean().default(false),
    evidence: z.array(z.unknown()).default([]),
  })
  .passthrough();

export const unifiedDatasetRecordSchema = z
  .object({
    record_index: z.number().int().nonnegative(),
    source_row: z.number().int().positive().nullable().optional(),
    values: z.record(z.string(), unifiedDataValueSchema),
    confidence: z.number().min(0).max(1),
    requires_human_review: z.boolean().default(false),
  })
  .passthrough();

export const tableColumnSpecSchema = z
  .object({
    key: z.string(),
    label: z.string(),
    index: z.number().int().nonnegative(),
    data_type: z.string(),
    unit: z.string().nullable().optional(),
    nullable: z.boolean().optional(),
  })
  .passthrough();

export const unifiedDatasetSpecSchema = z
  .object({
    dataset_id: z.string(),
    name: z.string(),
    filename: z.string(),
    source_container_type: z.string(),
    source_kind: z.string(),
    table_kind: z.string(),
    row_count: z.number().int().nonnegative(),
    column_count: z.number().int().nonnegative(),
    columns: z.array(tableColumnSpecSchema).default([]),
    records: z.array(unifiedDatasetRecordSchema).default([]),
    confidence: z.number().min(0).max(1),
    requires_human_review: z.boolean().default(false),
    review_status: z.string(),
    review_reasons: z.array(z.string()).default([]),
    warnings: z.array(z.string()).default([]),
    reviewed_by: z.string().nullable().optional(),
    reviewed_at: z.string().nullable().optional(),
    review_notes: z.string().nullable().optional(),
  })
  .passthrough();

export const unifiedIngestionResultSchema = z
  .object({
    filename: z.string(),
    pipeline_status: z.string(),
    datasets: z.array(unifiedDatasetSpecSchema).default([]),
    review_required_count: z.number().int().nonnegative().default(0),
    warnings: z.array(z.string()).default([]),
    errors: z.array(z.string()).default([]),
  })
  .passthrough();

export type UnifiedDataValue = z.infer<typeof unifiedDataValueSchema>;
export type UnifiedDatasetRecord = z.infer<typeof unifiedDatasetRecordSchema>;
export type UnifiedDatasetSpec = z.infer<typeof unifiedDatasetSpecSchema>;
export type UnifiedIngestionResult = z.infer<typeof unifiedIngestionResultSchema>;

export interface DatasetCorrection {
  record_index: number;
  column_key: string;
  corrected_value: unknown;
  note?: string;
}

export interface HumanReviewRequest {
  decision: "approve" | "reject";
  reviewer: string;
  notes?: string;
  corrections: DatasetCorrection[];
}
