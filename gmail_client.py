"""Gmail API wrapper: poll unread inbox, reply in-thread with optional ZIP attachment."""
import base64
import os
from dataclasses import dataclass
from email import encoders as _encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import List, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.send",
]


@dataclass
class IncomingEmail:
    gmail_id: str
    thread_id: str
    from_addr: str
    subject: str
    body: str
    message_id_header: str  # RFC Message-ID used to thread the reply

    @property
    def from_name(self) -> str:
        name = self.from_addr.split("<")[0].strip().strip('"')
        return name.split()[0] if name else "User"

    @property
    def from_email(self) -> str:
        if "<" in self.from_addr and ">" in self.from_addr:
            return self.from_addr.split("<")[1].split(">")[0].strip()
        return self.from_addr.strip()


def get_service():
    creds: Optional[Credentials] = None
    if os.path.exists(config.GMAIL_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(config.GMAIL_TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                config.GMAIL_CREDENTIALS_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(config.GMAIL_TOKEN_PATH, "w") as fh:
            fh.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _extract_body(payload) -> str:
    """Walk the MIME tree; prefer text/plain, fallback to text/html."""
    def decode(data: str) -> str:
        return base64.urlsafe_b64decode(data.encode()).decode("utf-8", "ignore")

    mime = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if mime == "text/plain" and body_data:
        return decode(body_data)

    html_fallback = ""
    for part in payload.get("parts", []) or []:
        text = _extract_body(part)
        if part.get("mimeType") == "text/plain" and text:
            return text
        if part.get("mimeType") == "text/html" and text:
            html_fallback = text
    return html_fallback


def list_unread(service, max_results: int = 10) -> List[IncomingEmail]:
    resp = (
        service.users()
        .messages()
        .list(userId="me", q="is:unread in:inbox", maxResults=max_results)
        .execute()
    )
    emails: List[IncomingEmail] = []
    for ref in resp.get("messages", []):
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in msg["payload"].get("headers", [])
        }
        emails.append(
            IncomingEmail(
                gmail_id=msg["id"],
                thread_id=msg["threadId"],
                from_addr=headers.get("from", ""),
                subject=headers.get("subject", ""),
                body=_extract_body(msg["payload"]),
                message_id_header=headers.get("message-id", ""),
            )
        )
    return emails


_ATTACH_LIMIT_BYTES = 20 * 1024 * 1024  # 20 MB — Gmail API raw-message limit


def send_reply(
    service,
    to_addr: str,
    subject: str,
    body: str,
    thread_id: str,
    in_reply_to: str,
    attachment: Optional[Path] = None,
):
    subj = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    # Decide whether to include the attachment
    attach_data: Optional[bytes] = None
    attach_name: Optional[str] = None
    if attachment and attachment.exists():
        size = attachment.stat().st_size
        if size <= _ATTACH_LIMIT_BYTES:
            attach_data = attachment.read_bytes()
            attach_name = attachment.name
        else:
            # Too large for Gmail API raw send — note it in the body
            body += (
                f"\n\n(The ZIP file was {size // (1024*1024)} MB — too large to "
                f"attach directly. Please ask me to send individual documents.)"
            )

    # Build message with MIMEMultipart (reliable with the Gmail API)
    if attach_data:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body, "plain"))
        part = MIMEBase("application", "zip")
        part.set_payload(attach_data)
        _encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{attach_name}"')
        msg.attach(part)
    else:
        msg = MIMEText(body, "plain")

    msg["To"] = to_addr
    msg["Subject"] = subj
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(
        userId="me", body={"raw": raw, "threadId": thread_id}
    ).execute()
    print(f"[gmail] sent reply to {to_addr}")


def mark_read(service, gmail_id: str):
    service.users().messages().modify(
        userId="me", id=gmail_id, body={"removeLabelIds": ["UNREAD"]}
    ).execute()
