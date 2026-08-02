# Human Review Frontend Integration

This patch adds a human-review gate before presentation generation.

## New flow

```text
Upload files + prompt
→ call ingestion /process for each file
→ if every dataset is trusted
   → create generation job
→ if any dataset requires_human_review
   → /review
   → compare source file and extracted table
   → approve or reject each dataset
   → only after all are approved, create generation job
```

## Files added

```text
src/frontend/src/api/ingestionApi.ts
src/frontend/src/schemas/ingestionSchema.ts
src/frontend/src/pages/ReviewPage.tsx
src/frontend/src/components/review/SourcePreview.tsx
src/frontend/src/components/review/DatasetReviewPanel.tsx
```

## Files changed

```text
src/frontend/src/pages/GeneratePage.tsx
src/frontend/src/components/GenerateForm.tsx
src/frontend/src/app/router.tsx
src/frontend/src/types/index.ts
src/frontend/src/i18n/translations.ts
src/frontend/.env.example
```

## Current API path

The current backend has both:

```python
router = APIRouter(prefix="/ingestion")
```

and:

```python
app.include_router(ingestion_router, prefix="/ingestion")
```

so the currently deployed path is:

```text
/ingestion/ingestion/process
/ingestion/ingestion/review-dataset
```

The frontend therefore uses:

```env
VITE_INGESTION_BASE_PATH=/ingestion
```

After the backend prefix is fixed, set:

```env
VITE_INGESTION_BASE_PATH=/ingestion
```

## Important backend limitation

The current `review-dataset` endpoint is stateless. It returns the reviewed Dataset, but does not save it. The existing `/api/v1/jobs/generate` endpoint then re-reads the original uploaded files.

Therefore this patch currently provides a real **approval gate in the frontend**, but reviewed/corrected Dataset values are not persisted into the generation job yet.

For production, add one of these backend designs:

1. Persist reviewed datasets by batch/job ID, then let the job runner consume the reviewed datasets.
2. Add `POST /api/v1/jobs/generate-reviewed` that receives the approved datasets and prompt.
3. Create a pending job before review, pause after ingestion, and continue the same job after all reviews are approved.

Option 3 is the cleanest long-term architecture because the ingestion result, review audit trail, and generation job share the same job ID.
