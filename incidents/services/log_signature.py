"""
Extract a normalized error signature from raw log text for deduplication / grouping.
"""
import hashlib
import re


def extract_error_signature(log_text: str) -> str:
    if not log_text or not str(log_text).strip():
        return ""

    text = str(log_text).strip()
    # Prefer strong log line signals first (CRITICAL > FATAL > ERROR per line patterns).
    line_patterns = [
        r"(?m)^.*\bCRITICAL\b.*$",
        r"(?m)^.*\bFATAL\b.*$",
        r"(?m)^.*\bERROR\b.*$",
        r"(?m)^.*\bException\b.*$",
        r"(?m)^.*Traceback\b.*$",
        r"(?i)(connection\s+refused|timed?\s*out|timeout|OOMKilled|out\s+of\s+memory|"
        r"502\s+bad\s+gateway|503\s+service\s+unavailable|504\s+gateway\s+timeout).{0,120}",
    ]
    for pat in line_patterns:
        m = re.search(pat, text)
        if m:
            sig = re.sub(r"\s+", " ", m.group(0).strip())
            return sig[:500]

    digest = hashlib.sha256(text[:4000].encode("utf-8", errors="ignore")).hexdigest()[:40]
    return f"hash:{digest}"
