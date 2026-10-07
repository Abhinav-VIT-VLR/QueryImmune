from __future__ import annotations

import re


_PATTERNS = (
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_ID]"),
    (re.compile(r"\bSYN-(?:PAT|LIC)-[A-Z0-9-]+\b", re.IGNORECASE), "[REDACTED_RECORD_ID]"),
    (re.compile(r"\b(?:MRN|patient\s*id|record\s*(?:number|no\.?))\s*[:#=]?\s*[A-Z0-9-]{4,}\b", re.IGNORECASE), "[REDACTED_RECORD_ID]"),
    (re.compile(r"(?<!\w)(?!\d{4}-\d{2}-\d{2}\b)(?:\+?\d[\d .()-]{8,}\d)(?!\w)"), "[REDACTED_NUMBER]"),
    (re.compile(r"\b(?:patient\s+named|patient\s+name\s+is|for)\s+(?:(?:Dr|Doctor|Patient)\.?\s+)?[A-Z][a-z]{1,30}(?:\s+[A-Z][a-z]{1,30})?\b"), "[REDACTED_PERSON]"),
    (re.compile(r"\b(?:DOB|date\s+of\s+birth)\s*(?:is|:|=)?\s*\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b", re.IGNORECASE), "[REDACTED_DOB]"),
)


def redact_direct_identifiers(value: str) -> str:
    """Best-effort scrub of common direct identifiers before an external model call."""
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    return value
