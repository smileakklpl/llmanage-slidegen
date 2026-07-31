"""Durable object storage adapters."""

from .s3_storage import S3ConfigurationError, S3ObjectStorage

__all__ = ["S3ConfigurationError", "S3ObjectStorage"]
