import re

EMAIL_PATTERN = re.compile(r"\b[\w.\-+%]+@[\w.\-]+\.[A-Za-z]{2,}\b")
IPV4_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
BEARER_TOKEN_PATTERN = re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b", re.IGNORECASE)
DB_URL_PATTERN = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mongodb|redis|mariadb|sqlserver)://[^\s\"'<>]+",
    re.IGNORECASE,
)
CC_PATTERN = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
# Generic secret-like strings (20+ chars) while avoiding obvious timestamps/uuids.
API_KEY_PATTERN = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_\-]{19,}\b")


def _mask_credit_card(match: re.Match) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 13 or len(digits) > 19:
        return raw
    return f"[MASKED_CREDIT_CARD_****{digits[-4:]}]"


def _mask_api_key(match: re.Match) -> str:
    token = match.group(0)
    lower = token.lower()
    if lower.startswith("http") or lower.startswith("error") or lower.startswith("warning"):
        return token
    return "[MASKED_API_KEY]"


def mask_pii(text: str) -> str:
    if not text:
        return text

    masked = text
    masked = BEARER_TOKEN_PATTERN.sub("[MASKED_TOKEN]", masked)
    masked = DB_URL_PATTERN.sub("[MASKED_DB_URL]", masked)
    masked = CC_PATTERN.sub(_mask_credit_card, masked)
    masked = EMAIL_PATTERN.sub("[MASKED_EMAIL]", masked)
    masked = IPV4_PATTERN.sub("[MASKED_IP]", masked)
    masked = API_KEY_PATTERN.sub(_mask_api_key, masked)

    # Keep previous key=value protection for known secret labels.
    masked = re.sub(
        r"(password|token|api_key|key|secret)\s*=\s*[^\s]+",
        r"\1=[REDACTED]",
        masked,
        flags=re.IGNORECASE,
    )
    return masked