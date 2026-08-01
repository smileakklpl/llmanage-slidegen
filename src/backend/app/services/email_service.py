"""Email service with SES integration.

Supports two modes controlled by the EMAIL_PROVIDER environment variable:
- "mock" (default): No actual email sent, returns success message
- "ses": Uses AWS SES to send real emails with attachments

Environment variables:
- EMAIL_PROVIDER: "mock" or "ses" (default: "mock")
- AWS_REGION: AWS region for SES (default: "us-east-1")
- SES_SENDER_EMAIL: Override sender email (optional, uses form input if not set)
"""

import os
import tempfile
from email import encoders
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# Output directory for job artifacts (must match job_runner.py)
_OUTPUT_BASE = Path(tempfile.gettempdir()) / "slidegen_outputs"


def _get_provider() -> str:
    return (os.getenv("EMAIL_PROVIDER") or "mock").strip().lower()


def _get_region() -> str:
    return os.getenv("AWS_REGION") or "us-east-1"


async def send_email(
    *,
    job_id: str,
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    artifact_filenames: list[str],
    extra_attachments: list[tuple[str, bytes]],
) -> dict:
    """Send an email with optional attachments.

    Args:
        job_id: Job ID to locate artifact files.
        sender: Sender email address.
        recipients: List of recipient email addresses.
        subject: Email subject line.
        body: Email body text.
        artifact_filenames: List of artifact filenames to attach (e.g. ["deck.pptx"]).
        extra_attachments: List of (filename, content_bytes) for user-uploaded attachments.

    Returns:
        Dict with job_id, sender, recipients, subject, attachment_count, message.
    """
    provider = _get_provider()

    # Collect all attachment data
    all_attachments: list[tuple[str, bytes]] = []

    # Load job artifact files from disk
    for filename in artifact_filenames:
        file_path = _OUTPUT_BASE / job_id / filename
        if file_path.exists():
            all_attachments.append((filename, file_path.read_bytes()))
        else:
            logger.warning("Artifact file not found: %s", file_path)

    # Add user-uploaded extra attachments
    all_attachments.extend(extra_attachments)

    total_attachments = len(all_attachments)

    if provider == "ses":
        message = await _send_via_ses(
            sender=sender,
            recipients=recipients,
            subject=subject,
            body=body,
            attachments=all_attachments,
        )
    else:
        message = (
            f"模擬寄送完成，由 {sender} 寄送給 {len(recipients)} 位收件者"
            + (f"，附帶 {total_attachments} 個附件" if total_attachments else "")
        )

    # Store all email attachments to S3 (under emails/{job_id}/)
    from app.services.s3_service import store_email_attachment
    for filename, content in all_attachments:
        store_email_attachment(job_id, content, filename)

    return {
        "job_id": job_id,
        "sender": sender,
        "recipients": recipients,
        "subject": subject,
        "attachment_count": total_attachments,
        "message": message,
    }


async def _send_via_ses(
    *,
    sender: str,
    recipients: list[str],
    subject: str,
    body: str,
    attachments: list[tuple[str, bytes]],
) -> str:
    """Send email using AWS SES with raw email (supports attachments)."""
    import asyncio

    import boto3

    # Override sender if configured (SES requires verified sender)
    ses_sender = os.getenv("SES_SENDER_EMAIL") or sender
    region = _get_region()

    # Build MIME message
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject or "智匯數據簡報神器 — 簡報寄送"
    msg["From"] = ses_sender
    msg["To"] = ", ".join(recipients)
    # Reply-To 設成使用者填的 email，收件者回信時會回給使用者
    if sender != ses_sender:
        msg["Reply-To"] = sender

    # Body
    body_text = body or "您好，請查收附件中的簡報分析結果。"
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    # Attachments
    for filename, content in attachments:
        part = MIMEApplication(content)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        encoders.encode_base64(part)
        msg.attach(part)

    # Send via SES (boto3 is sync, run in thread)
    def _do_send():
        client = boto3.client("ses", region_name=region)
        response = client.send_raw_email(
            Source=ses_sender,
            Destinations=recipients,
            RawMessage={"Data": msg.as_string()},
        )
        return response.get("MessageId", "unknown")

    try:
        message_id = await asyncio.to_thread(_do_send)
        logger.info("SES email sent: message_id=%s to=%s", message_id, recipients)
        return f"寄送成功（MessageId: {message_id}）"
    except Exception as exc:
        logger.error("SES send failed: %s", exc)
        raise RuntimeError(f"SES 寄送失敗：{exc}") from exc
