import logging
import os
import tempfile

log = logging.getLogger(__name__)

DEFAULT_QUERY = "has:attachment subject:(report OR export OR inventory OR orders)"


async def fetch_report_attachments(
    gmail_connector,
    query: str = DEFAULT_QUERY,
    max_results: int = 20,
) -> list[dict]:
    """Download attachments from emails matching query and return file metadata."""
    emails = await gmail_connector.get_recent_emails(query=query, max_results=max_results)
    results: list[dict] = []

    for email in emails:
        attachments = email.get("attachments", [])
        for attachment in attachments:
            filename = attachment.get("filename", "attachment")
            data = attachment.get("data", b"")
            if not data:
                continue

            suffix = os.path.splitext(filename)[1] or ".bin"
            tmp = tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, prefix="secretary_"
            )
            try:
                if isinstance(data, str):
                    import base64
                    data = base64.urlsafe_b64decode(data + "==")
                tmp.write(data)
                tmp.flush()
                results.append(
                    {
                        "filename": filename,
                        "path": tmp.name,
                        "message_id": email.get("id", ""),
                        "date": email.get("date", ""),
                    }
                )
                log.info("email_ingestion: saved attachment %s → %s", filename, tmp.name)
            finally:
                tmp.close()

    return results
