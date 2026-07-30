/**
 * Zod schema for the health check API response.
 * Matches GET /api/v1/health response format.
 */

import { z } from "zod";

/** Health check response schema. */
export const healthResponseSchema = z.object({
  status: z.string(),
  service: z.string(),
});

// Inferred TypeScript type
export type HealthResponse = z.infer<typeof healthResponseSchema>;
