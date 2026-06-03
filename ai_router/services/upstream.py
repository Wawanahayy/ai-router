"""Provider upstream URL and authentication helpers."""
import json
import logging
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

logger = logging.getLogger(__name__)


def provider_format(provider: dict) -> str:
    """Return the request/response format handled by the router."""
    if provider.get("type") == "claude-cli":
        return provider.get("request_format") or "anthropic-compatible"
    return provider.get("request_format") or provider.get("type") or "openai-compatible"


def endpoint_path(provider: dict, kind: str) -> str:
    fmt = provider_format(provider)
    if kind == "chat":
        default = "/messages" if fmt == "anthropic-compatible" else "/chat/completions"
        return provider.get("chat_path") or default
    if kind == "models":
        return provider.get("models_path") or "/models"
    return ""


def join_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    clean_path = path or ""
    if not clean_path:
        return base
    if clean_path.startswith("http://") or clean_path.startswith("https://"):
        return clean_path
    return f"{base}/{clean_path.lstrip('/')}"


def parse_extra_headers(provider: dict) -> dict:
    raw = provider.get("extra_headers")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception as e:
        logger.warning("Invalid extra_headers for provider %s: %s", provider.get("id"), e)
        return {}


def _add_query_param(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query[name] = value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def apply_auth(provider: dict, key_value: str, headers: dict, url: str) -> tuple[dict, str]:
    """Apply a provider API key using configurable auth placement."""
    auth_type = (provider.get("auth_type") or "").strip().lower()
    fmt = provider_format(provider)
    if not auth_type:
        auth_type = "x-api-key" if fmt == "anthropic-compatible" else "bearer"

    if auth_type in ("none", "noauth"):
        return headers, url

    if auth_type in ("query", "query-param", "url"):
        param = provider.get("key_query_param") or "key"
        return headers, _add_query_param(url, param, key_value)

    header = provider.get("auth_header") or ""
    prefix = provider.get("auth_prefix")

    if auth_type == "bearer":
        header = header or "Authorization"
        prefix = "Bearer " if prefix is None or prefix == "" else prefix
    elif auth_type == "x-api-key":
        header = header or "x-api-key"
        prefix = "" if prefix is None else prefix
    elif auth_type == "api-key":
        header = header or "api-key"
        prefix = "" if prefix is None else prefix
    elif auth_type == "header":
        header = header or "Authorization"
        prefix = "" if prefix is None else prefix
    else:
        header = header or auth_type
        prefix = "" if prefix is None else prefix

    headers[header] = f"{prefix}{key_value}"
    return headers, url


def build_headers(provider: dict, key_value: str | None = None, event_stream: bool = False) -> dict:
    headers = {
        "Content-Type": "application/json",
        **parse_extra_headers(provider),
    }
    if event_stream:
        headers.setdefault("Accept", "text/event-stream")
    fmt = provider_format(provider)
    if fmt == "anthropic-compatible":
        headers.setdefault("anthropic-version", provider.get("anthropic_version") or "2023-06-01")
    return headers


def build_request(provider: dict, kind: str, key_value: str | None = None, event_stream: bool = False) -> dict:
    url = join_url(provider["base_url"], endpoint_path(provider, kind))
    headers = build_headers(provider, key_value, event_stream)
    if key_value is not None:
        headers, url = apply_auth(provider, key_value, headers, url)
    return {"url": url, "headers": headers}
