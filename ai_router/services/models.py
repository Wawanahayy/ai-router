"""Model listing and upstream model fetch helpers."""
import logging
import time

import httpx

from .. import db
from .upstream import build_request, provider_format

logger = logging.getLogger(__name__)

_models_cache: dict = {}
_MODELS_CACHE_TTL = 300


def invalidate_models_cache(provider_id: str = None):
    """Clear models cache. If provider_id is given, clear only that provider."""
    if provider_id:
        _models_cache.pop(provider_id, None)
    else:
        _models_cache.clear()


def get_effective_prefix(provider: dict) -> str:
    if provider.get("prefix_enabled", 0):
        return provider.get("prefix", "") or ""
    return ""


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
            if key and provider_format(p) != "anthropic-compatible":
                now = time.time()
                cached = _models_cache.get(p["id"])
                if cached and (now - cached["fetched_at"]) < _MODELS_CACHE_TTL:
                    upstream_models = cached["data"]
                else:
                    req = build_request(p, "models", key["key_value"])
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(req["url"], headers=req["headers"])
                    if resp.status_code == 200:
                        data = resp.json()
                        upstream_models = data.get("data", [])
                        _models_cache[p["id"]] = {"data": upstream_models, "fetched_at": now}
                    else:
                        upstream_models = []

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
    if provider_format(provider) == "anthropic-compatible":
        return []

    req = build_request(provider, "models", key["key_value"])

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(req["url"], headers=req["headers"])
    if resp.status_code == 200:
        data = resp.json()
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    return []
