"""S3 storage service for file persistence.

Stores files in three prefixes within one S3 bucket:
- uploads/{job_id}/       — user-uploaded source files (xlsx)
- outputs/{job_id}/       — generated artifacts (deck.pptx, deck_data.xlsx)
- emails/{job_id}/        — email attachments sent to recipients

Controlled by environment variables:
- S3_ENABLED: "true" to enable S3 uploads (default: "false", files stay local only)
- S3_BUCKET: bucket name (required when S3_ENABLED=true)
- AWS_REGION: AWS region (default: "us-east-1")
"""

import os
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)


def _is_enabled() -> bool:
    return os.getenv("S3_ENABLED", "false").strip().lower() == "true"


def _get_bucket() -> str:
    bucket = os.getenv("S3_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("S3_BUCKET 環境變數未設定")
    return bucket


def _get_region() -> str:
    return os.getenv("AWS_REGION") or "us-east-1"


def _get_client():
    import boto3
    return boto3.client("s3", region_name=_get_region())


def upload_file(local_path: Path, s3_key: str) -> str | None:
    """Upload a local file to S3.

    Args:
        local_path: Path to the local file.
        s3_key: Full S3 key (prefix + filename).

    Returns:
        S3 URI (s3://bucket/key) on success, None if S3 is disabled.
    """
    if not _is_enabled():
        return None

    if not local_path.exists():
        logger.warning("File not found, skipping S3 upload: %s", local_path)
        return None

    bucket = _get_bucket()
    client = _get_client()

    try:
        client.upload_file(str(local_path), bucket, s3_key)
        uri = f"s3://{bucket}/{s3_key}"
        logger.info("Uploaded to S3: %s", uri)
        return uri
    except Exception as exc:
        logger.error("S3 upload failed for %s: %s", s3_key, exc)
        return None


def upload_bytes(content: bytes, s3_key: str, filename: str = "") -> str | None:
    """Upload bytes directly to S3.

    Args:
        content: File content as bytes.
        s3_key: Full S3 key (prefix + filename).
        filename: Original filename (for logging only).

    Returns:
        S3 URI on success, None if S3 is disabled.
    """
    if not _is_enabled():
        return None

    bucket = _get_bucket()
    client = _get_client()

    try:
        client.put_object(Bucket=bucket, Key=s3_key, Body=content)
        uri = f"s3://{bucket}/{s3_key}"
        logger.info("Uploaded to S3: %s (%s)", uri, filename)
        return uri
    except Exception as exc:
        logger.error("S3 upload failed for %s: %s", s3_key, exc)
        return None


# ---------------------------------------------------------------------------
# Convenience functions for each prefix
# ---------------------------------------------------------------------------

def store_upload(job_id: str, local_path: Path, filename: str) -> str | None:
    """Store a user-uploaded file under uploads/{job_id}/."""
    s3_key = f"uploads/{job_id}/{filename}"
    return upload_file(local_path, s3_key)


def store_output(job_id: str, local_path: Path, filename: str) -> str | None:
    """Store a generated artifact under outputs/{job_id}/."""
    s3_key = f"outputs/{job_id}/{filename}"
    return upload_file(local_path, s3_key)


def store_email_attachment(job_id: str, content: bytes, filename: str) -> str | None:
    """Store an email attachment under emails/{job_id}/."""
    s3_key = f"emails/{job_id}/{filename}"
    return upload_bytes(content, s3_key, filename)
