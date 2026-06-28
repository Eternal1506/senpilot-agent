"""Turn a free-text email into a structured request: (matter_number, doc_type).

Default: regex (fast, reliable for the M##### + doc-type format).
If ANTHROPIC_API_KEY is set, Claude is tried first; regex is the fallback.
"""
import json
import re
from typing import Optional, Tuple

import config

MATTER_RE = re.compile(r"\bM\d{5}\b", re.IGNORECASE)


def _match_doc_type(text: str) -> Optional[str]:
    low = text.lower()
    # Longest-match first so "Other Documents" / "Key Documents" beat "Documents"
    for dt in sorted(config.DOC_TYPES, key=len, reverse=True):
        if dt.lower() in low:
            return dt
    return None


def parse_rule_based(text: str) -> Tuple[Optional[str], Optional[str]]:
    text = text or ""
    m = MATTER_RE.search(text)
    matter = m.group(0).upper() if m else None
    return matter, _match_doc_type(text)


def parse_with_claude(text: str) -> Tuple[Optional[str], Optional[str]]:
    from anthropic import Anthropic
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    prompt = (
        "Extract the matter number and document type from this email. "
        f"Matter numbers look like M12205. Document type is exactly one of: "
        f"{', '.join(config.DOC_TYPES)}.\n"
        'Respond ONLY with JSON: {"matter": "...", "doc_type": "..."} '
        "using null for anything missing.\n\n"
        f"Email:\n{text}"
    )
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")
    raw = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    matter = (data.get("matter") or None)
    doc_type = (data.get("doc_type") or None)
    if matter:
        matter = matter.upper()
    if doc_type:
        for dt in config.DOC_TYPES:
            if dt.lower() == doc_type.lower():
                doc_type = dt
                break
    return matter, doc_type


def parse_request(text: str) -> Tuple[Optional[str], Optional[str]]:
    if config.ANTHROPIC_API_KEY:
        try:
            matter, doc_type = parse_with_claude(text)
            if matter and doc_type:
                return matter, doc_type
        except Exception as e:
            print(f"[parser] Claude parse failed, falling back to regex: {e}")
    return parse_rule_based(text)
