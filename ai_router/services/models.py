"""Model listing and upstream model fetch helpers."""
import logging
import os
import time
from collections import OrderedDict

import httpx

from .. import db
from .upstream import build_request, provider_format

logger = logging.getLogger(__name__)

# Bounded LRU cache. Per-provider upstream /models responses are stored
# here and evicted in insertion order once we exceed the size cap.
_models_cache: "OrderedDict[str, dict]" = OrderedDict()
_MODELS_CACHE_TTL = 300
_MODELS_CACHE_MAX = max(1, int(os.getenv("AI_ROUTER_MODELS_CACHE_MAX", "64")))


def _cache_get(provider_id: str):
    """Return cached entry, mark as recently used. Returns None if absent/expired."""
    now = time.time()
    entry = _models_cache.get(provider_id)
    if not entry:
        return None
    if (now - entry["fetched_at"]) >= _MODELS_CACHE_TTL:
        _models_cache.pop(provider_id, None)
        return None
    _models_cache.move_to_end(provider_id)
    return entry


def _cache_put(provider_id: str, upstream_models: list):
    """Store upstream models with LRU eviction."""
    now = time.time()
    _models_cache[provider_id] = {"data": upstream_models, "fetched_at": now}
    _models_cache.move_to_end(provider_id)
    while len(_models_cache) > _MODELS_CACHE_MAX:
        evicted_id, _ = _models_cache.popitem(last=False)
        logger.debug("Models cache evicted provider_id=%s (size cap=%d)", evicted_id, _MODELS_CACHE_MAX)


def invalidate_models_cache(provider_id: str = None):
    """Clear models cache. If provider_id is given, clear only that provider."""
    if provider_id:
        _models_cache.pop(provider_id, None)
    else:
        _models_cache.clear()
    return {"cleared": 1 if provider_id else len(_models_cache), "max": _MODELS_CACHE_MAX}


def get_effective_prefix(provider: dict) -> str:
    if provider.get("prefix_enabled", 0):
        return provider.get("prefix", "") or ""
    return ""


def _model_id_from_item(item):
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return ""
    for key in ("id", "name", "model", "model_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_models_payload(data) -> list[dict]:
    """Normalize OpenAI/Anthropic-compatible model list shapes."""
    if isinstance(data, dict):
        items = data.get("data")
        if items is None:
            items = data.get("models")
        if items is None:
            items = data.get("model_list")
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if not isinstance(items, list):
        return []

    models = []
    seen = set()
    for item in items:
        mid = _model_id_from_item(item)
        if not mid or mid in seen:
            continue
        seen.add(mid)
        models.append({
            "id": mid,
            "created": item.get("created", 0) if isinstance(item, dict) else 0,
        })
    return models


async def _fetch_models_from_upstream(provider: dict, key: dict, timeout: float = 15.0) -> list[dict]:
    requests = [("configured", build_request(provider, "models", key["key_value"]))]

    # Anthropic-native chat uses x-api-key, but many Anthropic-compatible
    # gateways expose an OpenAI-style /models endpoint that only accepts
    # Authorization: Bearer. Preserve the configured request first, then retry
    # discovery with Bearer auth without changing the provider's chat path.
    fmt = provider_format(provider)
    auth_type = (provider.get("auth_type") or "").strip().lower()
    if fmt == "anthropic-compatible" and auth_type in ("", "x-api-key"):
        bearer_provider = dict(provider)
        bearer_provider.update({
            "auth_type": "bearer",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer ",
        })
        requests.append(("bearer-fallback", build_request(bearer_provider, "models", key["key_value"])))

    last_response = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for auth_mode, req in requests:
            try:
                resp = await client.get(req["url"], headers=req["headers"])
            except httpx.HTTPError as exc:
                logger.warning(
                    "Failed to fetch upstream models provider=%s format=%s auth_mode=%s error=%s",
                    provider.get("id"),
                    fmt,
                    auth_mode,
                    exc.__class__.__name__,
                )
                continue
            last_response = resp
            if resp.status_code != 200:
                logger.warning(
                    "Failed to fetch upstream models provider=%s format=%s auth_mode=%s status=%s body=%s",
                    provider.get("id"),
                    fmt,
                    auth_mode,
                    resp.status_code,
                    resp.text[:300],
                )
                continue
            try:
                data = resp.json()
            except Exception:
                logger.warning(
                    "Invalid upstream models JSON provider=%s auth_mode=%s body=%s",
                    provider.get("id"),
                    auth_mode,
                    resp.text[:300],
                )
                continue
            models = normalize_models_payload(data)
            if models or auth_mode == requests[-1][0]:
                return models

    if last_response is None:
        logger.warning("No upstream model discovery request built for provider=%s", provider.get("id"))
    return []


async def proxy_models(provider_id: str = None):
    """List available models."""
    if provider_id:
        provider = await db.get_provider(provider_id)
        if not provider:
            return {"error": "Provider not found"}, 404
        providers = [provider]
    else:
        providers = await db.list_providers()

    models = []
    for p in providers:
        if not p["is_active"]:
            continue
        effective_prefix = get_effective_prefix(p)

        for a in p.get("aliases", []):
            if not a.get("is_active", 1):
                continue
            model_id = f"{effective_prefix}/{a['model_id']}" if effective_prefix else a["model_id"]
            models.append({
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": p["name"],
            })

        try:
            key = await db.get_alive_key(p["id"])
            if key:
                cached = _cache_get(p["id"])
                if cached is not None:
                    upstream_models = cached["data"]
                else:
                    upstream_models = await _fetch_models_from_upstream(p, key, timeout=10.0)
                    _cache_put(p["id"], upstream_models)

                for m in upstream_models:
                    mid = m.get("id", "")
                    prefixed = f"{effective_prefix}/{mid}" if effective_prefix else mid
                    if not any(x["id"] == prefixed for x in models):
                        models.append({
                            "id": prefixed,
                            "object": "model",
                            "created": m.get("created", 0),
                            "owned_by": p["name"],
                        })
        except Exception as e:
            logger.warning("Failed to list upstream models for provider %s: %s", p.get("id"), e)

    combos = await db.list_combos()
    for c in combos:
        if c.get("is_active") and c.get("model_count", 0) > 0:
            combo_name = c["name"].lower()
            if not any(m["id"] == combo_name for m in models):
                models.append({
                    "id": combo_name,
                    "object": "model",
                    "created": 0,
                    "owned_by": "combo",
                })

    return {"object": "list", "data": models}, 200


async def fetch_upstream_models(provider: dict, key: dict):
    """Fetch models from upstream /models endpoint."""
    return [m["id"] for m in await _fetch_models_from_upstream(provider, key, timeout=15.0)]
