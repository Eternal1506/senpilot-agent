"""Orchestration: parser → scraper → summary → reply."""
import gmail_client
import config
from request_parser import parse_request
from scraper import fetch_matter
from summary import build_summary


def handle_email(service, email: gmail_client.IncomingEmail) -> None:
    print(f"\n[agent] from={email.from_email}  subject={email.subject!r}")
    matter, doc_type = parse_request(f"{email.subject}\n{email.body}")
    print(f"[agent] parsed  matter={matter}  doc_type={doc_type}")

    if not matter or not doc_type:
        missing = []
        if not matter:
            missing.append("a matter number (e.g. M12205)")
        if not doc_type:
            missing.append(f"a document type ({', '.join(config.DOC_TYPES)})")
        body = (
            f"Hi {email.from_name}, I couldn't find {' and '.join(missing)} in your "
            f"message. Could you resend with both? Example: "
            f'"Can you give me Other Documents files from M12205?"'
        )
        gmail_client.send_reply(
            service, email.from_email, email.subject, body,
            email.thread_id, email.message_id_header,
        )
        return

    result = fetch_matter(matter, doc_type)
    body = build_summary(result, user_first_name=email.from_name)
    gmail_client.send_reply(
        service,
        to_addr=email.from_email,
        subject=email.subject,
        body=body,
        thread_id=email.thread_id,
        in_reply_to=email.message_id_header,
        attachment=result.zip_path,
    )
    print(f"[agent] replied to {email.from_email}")
