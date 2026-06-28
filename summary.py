"""Compose the reply email body from the scrape result.

Deterministic template by default (mirrors the challenge's example email exactly).
Optional Claude polish if ANTHROPIC_API_KEY is set.
"""
from typing import Dict

import config
from scraper import MatterResult


def _long_date(mmddyyyy: str) -> str:
    """Convert MM/DD/YYYY to 'April 7, 2025' — cross-platform (no %-d)."""
    try:
        from datetime import datetime
        dt = datetime.strptime(mmddyyyy, "%m/%d/%Y")
        return dt.strftime(f"%B {dt.day}, %Y")  # dt.day has no leading zero
    except Exception:
        return mmddyyyy


def _format_counts(counts: Dict[str, int]) -> str:
    """Produce e.g. '13 Exhibits, 5 Key Documents, 21 Other Documents, and no Transcripts or Recordings'."""
    found = [(dt, c) for dt, c in counts.items() if c > 0]
    zeros = [dt for dt, c in counts.items() if c == 0]

    parts = [f"{c} {dt}" for dt, c in found] or ["no documents"]
    found_str = ", ".join(parts)

    if zeros:
        if len(zeros) == 1:
            found_str += f", and no {zeros[0]}"
        elif len(zeros) == 2:
            found_str += f", and no {zeros[0]} or {zeros[1]}"
        else:
            found_str += ", and no " + ", ".join(zeros[:-1]) + f", or {zeros[-1]}"

    return found_str


def build_summary(result: MatterResult, user_first_name: str = "User") -> str:
    if result.error and not result.downloaded_files:
        return (
            f"Hi {user_first_name}, I ran into a problem fetching {result.requested_type} "
            f"for {result.matter}: {result.error}. "
            f"Could you double-check the matter number and document type and try again?"
        )

    title = result.title or f"matter {result.matter}"
    counts_str = _format_counts(result.counts)
    total = result.requested_total
    got = len(result.downloaded_files)

    if total == 0:
        dl_sentence = f"There were no {result.requested_type} to download."
    elif total <= config.MAX_DOCS:
        dl_sentence = (
            f"I downloaded all {got} {result.requested_type} "
            f"and am attaching them as a ZIP here."
        )
    else:
        dl_sentence = (
            f"I downloaded {got} out of the {total} {result.requested_type} "
            f"and am attaching them as a ZIP here."
        )

    bits = [f"Hi {user_first_name}, {result.matter} is about the {title}."]
    if result.type_ and result.category:
        bits.append(f"It relates to {result.type_} within the {result.category} category.")
    if result.date_received and result.date_final:
        bits.append(
            f"The matter had an initial filing on {_long_date(result.date_received)} "
            f"and a final filing on {_long_date(result.date_final)}."
        )
    bits.append(f"I found {counts_str}.")
    bits.append(dl_sentence)
    body = " ".join(bits)

    if config.ANTHROPIC_API_KEY:
        try:
            body = _polish_with_claude(body)
        except Exception as e:
            print(f"[summary] Claude polish skipped: {e}")
    return body


def _polish_with_claude(draft: str) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": (
                "Lightly polish this support email for tone and clarity. "
                "Keep every fact, number, and date exactly as written. "
                "Return only the email body, no preamble.\n\n" + draft
            ),
        }],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip() or draft
