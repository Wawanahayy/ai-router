ERROR_RULES = [
    {"text": "context_length", "kind": "context_limit", "fallback": True, "cooldown": 300, "type": "invalid_request_error"},
    {"text": "context length", "kind": "context_limit", "fallback": True, "cooldown": 300, "type": "invalid_request_error"},
    {"text": "input token exceed", "kind": "context_limit", "fallback": True, "cooldown": 300, "type": "invalid_request_error"},
    {"text": "too many tokens", "kind": "context_limit", "fallback": True, "cooldown": 300, "type": "invalid_request_error"},
    {"text": "prompt is too long", "kind": "context_limit", "fallback": True, "cooldown": 300, "type": "invalid_request_error"},
    {"text": "insufficient balance", "kind": "quota_exhausted", "fallback": True, "cooldown": 300, "type": "insufficient_quota"},
    {"text": "insufficient quota", "kind": "quota_exhausted", "fallback": True, "cooldown": 300, "type": "insufficient_quota"},
    {"text": "quota exceeded", "kind": "quota_exhausted", "fallback": True, "cooldown": 300, "type": "insufficient_quota"},
    {"text": "too many requests", "kind": "rate_limited", "fallback": True, "cooldown": 3, "type": "rate_limit_error"},
    {"text": "rate limit", "kind": "rate_limited", "fallback": True, "cooldown": 3, "type": "rate_limit_error"},
    {"text": "overloaded", "kind": "overloaded", "fallback": True, "cooldown": 5, "type": "api_error"},
    {"text": "capacity", "kind": "overloaded", "fallback": True, "cooldown": 5, "type": "api_error"},
    {"text": "unsupported", "kind": "unsupported_model", "fallback": False, "cooldown": 300, "type": "invalid_request_error"},
    {"text": "model not found", "kind": "unsupported_model", "fallback": False, "cooldown": 300, "type": "invalid_request_error"},
    {"status": 401, "kind": "auth_dead", "fallback": True, "cooldown": 300, "type": "authentication_error"},
    {"status": 403, "kind": "auth_dead", "fallback": True, "cooldown": 300, "type": "authentication_error"},
    {"status": 408, "kind": "timeout", "fallback": True, "cooldown": 30, "type": "timeout_error"},
    {"status": 429, "kind": "rate_limited", "fallback": True, "cooldown": 3, "type": "rate_limit_error"},
    {"status": 500, "kind": "overloaded", "fallback": True, "cooldown": 30, "type": "api_error"},
    {"status": 502, "kind": "overloaded", "fallback": True, "cooldown": 30, "type": "api_error"},
    {"status": 503, "kind": "overloaded", "fallback": True, "cooldown": 30, "type": "api_error"},
    {"status": 504, "kind": "timeout", "fallback": True, "cooldown": 30, "type": "timeout_error"},
    {"status": 524, "kind": "timeout", "fallback": True, "cooldown": 30, "type": "timeout_error"},
    {"status": 529, "kind": "overloaded", "fallback": True, "cooldown": 30, "type": "api_error"},
]


# Heuristic: raw upstream errors (Cloudflare HTML, Chinese provider error
# pages, etc) MUST NOT leak to chat agents — strip to a generic "upstream
# error" marker. Triggered by HTML markup, DOCTYPE, or non-Latin/CJK
# characters in the body.
def _sanitize_error(message: str, status_code: int = 0, max_len: int = 200) -> str:
    if not message:
        return "upstream error"
    snippet = str(message)
    # Cut at first newline (HTML pages are usually single-line once flattened)
    snippet = snippet.split("\n", 1)[0].strip()
    if not snippet:
        return "upstream error"
    # Detect HTML
    lowered = snippet.lower()
    if any(marker in lowered for marker in ("<!doctype", "<html", "<head", "<body", "<title")):
        return f"upstream error (status {status_code})" if status_code else "upstream error"
    # Detect high ratio of CJK / non-printable / control characters
    cjk = sum(1 for ch in snippet if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff" or "\uac00" <= ch <= "\ud7af")
    printable = sum(1 for ch in snippet if ch.isprintable() or ch in "\r\n\t")
    if cjk >= 3 and cjk * 3 >= printable:
        return f"upstream error (status {status_code})" if status_code else "upstream error"
    if printable < max(1, len(snippet) // 2):
        return f"upstream error (status {status_code})" if status_code else "upstream error"
    # Truncate to keep responses small
    if len(snippet) > max_len:
        snippet = snippet[:max_len].rstrip() + "…"
    return snippet


def classify_error(status_code: int, error_msg: str = ""):
    lowered = (error_msg or "").lower()
    for rule in ERROR_RULES:
        text = rule.get("text")
        if text and text in lowered:
            return rule
    for rule in ERROR_RULES:
        if rule.get("status") == status_code:
            return rule
    if status_code >= 500:
        return {"kind": "upstream_error", "fallback": True, "cooldown": 5, "type": "api_error"}
    return {"kind": "upstream_error", "fallback": False, "cooldown": 0, "type": "invalid_request_error"}


def error_kind(status_code: int, error_msg: str = ""):
    return classify_error(status_code, error_msg).get("kind", "upstream_error")


def fallbackable(status_code: int, error_msg: str = ""):
    return bool(classify_error(status_code, error_msg).get("fallback"))


def transient_wait_seconds(status_code: int, error_msg: str = ""):
    return int(classify_error(status_code, error_msg).get("cooldown") or 0)


def rate_limit_wait_seconds(attempt_count: int):
    steps = (3, 5, 10)
    return steps[min(max(attempt_count - 1, 0), len(steps) - 1)]


def openai_error_type(status_code: int, error_msg: str = ""):
    return classify_error(status_code, error_msg).get("type") or "invalid_request_error"


def error_response(message: str, status_code: int = 502, fallback_chain=None):
    sanitized = _sanitize_error(message, status_code=status_code)
    body = {
        "error": {
            "message": sanitized or "upstream error",
            "type": openai_error_type(status_code, message),
            "code": error_kind(status_code, message),
        }
    }
    if fallback_chain:
        body["fallback_chain"] = fallback_chain
    return body
