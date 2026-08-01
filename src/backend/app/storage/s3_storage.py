"""S3-backed persistence for uploads, outputs, stage dumps, and job JSON."""

from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from typing import Any, BinaryIO

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from core.contracts.generation import StoredObjectRef


class S3ConfigurationError(RuntimeError):
    """Raised when required S3 settings are absent."""


class S3ObjectStorage:
    """Thin S3 adapter. Every AWS client is constructed with an explicit region."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint_url: str | None = None,
        presign_expires_seconds: int = 3600,
    ) -> None:
        if not bucket.strip():
            raise S3ConfigurationError("S3_BUCKET 尚未設定")

        if not region.strip():
            raise S3ConfigurationError("AWS_REGION 尚未設定")

        self.bucket = bucket
        self.region = region
        self.presign_expires_seconds = presign_expires_seconds
        self._client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url or None,
            config=Config(signature_version="s3v4"),
        )

    def upload_fileobj(
        self,
        stream: BinaryIO,
        *,
        key: str,
        filename: str,
        content_type: str | None = None,
    ) -> StoredObjectRef:
        extra_args = {}

        if content_type:
            extra_args["ContentType"] = content_type

        position = stream.tell()
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        stream.seek(position)

        self._client.upload_fileobj(
            stream,
            self.bucket,
            key,
            ExtraArgs=extra_args or None,
        )
        return self._head_ref(
            key=key,
            filename=filename,
            sha256=digest.hexdigest(),
        )

    def upload_path(
        self,
        path: str | Path,
        *,
        key: str,
        content_type: str | None = None,
    ) -> StoredObjectRef:
        source = Path(path)
        detected = content_type or mimetypes.guess_type(source.name)[0]
        extra_args = {"ContentType": detected} if detected else None
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)

        self._client.upload_file(
            str(source),
            self.bucket,
            key,
            ExtraArgs=extra_args,
        )
        return self._head_ref(
            key=key,
            filename=source.name,
            sha256=digest.hexdigest(),
        )

    def download_path(self, key: str, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(target))
        return target

    def put_json(self, key: str, payload: dict[str, Any]) -> None:
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

    def put_json_ref(self, key: str, payload: dict[str, Any]) -> StoredObjectRef:
        """Persist JSON and return refreshed object metadata for durable references."""
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        return self._head_ref(
            key=key,
            filename=Path(key).name,
            sha256=digest,
        )

    def get_json(self, key: str) -> dict[str, Any] | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            code = str(error.response.get("Error", {}).get("Code", ""))

            if code in {"NoSuchKey", "404", "NotFound"}:
                return None

            raise

        payload = json.loads(response["Body"].read().decode("utf-8"))
        return payload if isinstance(payload, dict) else None

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def presigned_download_url(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=self.presign_expires_seconds,
        )

    def _head_ref(
        self,
        *,
        key: str,
        filename: str,
        sha256: str | None = None,
    ) -> StoredObjectRef:
        metadata = self._client.head_object(Bucket=self.bucket, Key=key)
        return StoredObjectRef(
            bucket=self.bucket,
            key=key,
            filename=filename,
            size_bytes=int(metadata.get("ContentLength") or 0),
            etag=str(metadata.get("ETag") or "").strip('"') or None,
            sha256=sha256,
        )
