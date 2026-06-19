import httpx


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


async def fetch_openrouter_pricing():
    headers = {"User-Agent": "ai-router/1.0"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
        resp = await client.get(OPENROUTER_MODELS_URL)
        resp.raise_for_status()
        models = resp.json().get("data") or []

    rows = []
    for model in models:
        model_id = model.get("id") or ""
        if not model_id:
            continue
        pricing = model.get("pricing") or {}
        context_window = model.get("context_length") or model.get("context_window") or model.get("max_context_length") or 0
        try:
            input_per_million = float(pricing.get("prompt") or 0) * 1_000_000
            output_per_million = float(pricing.get("completion") or 0) * 1_000_000
        except (TypeError, ValueError):
            input_per_million = 0
            output_per_million = 0
        try:
            context_window = int(context_window or 0)
        except (TypeError, ValueError):
            context_window = 0
        rows.append({
            "model_id": model_id,
            "input_per_million": input_per_million,
            "output_per_million": output_per_million,
            "context_window": context_window,
            "source": "openrouter_catalog",
        })
    return rows
