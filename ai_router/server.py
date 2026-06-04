"""AI Router FastAPI main server."""
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from . import db, proxy, config
import json
import os
import time

app = FastAPI(title="AI Router", version="2.0.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth helpers ---

async def dashboard_auth_config():
    require_login = await db.get_setting("require_login")
    password = await db.get_setting("login_password") or config.AUTH_PASSWORD
    return config.AUTH_ENABLED or require_login == "true", password


async def check_dashboard_auth(request: Request):
    """Check auth for dashboard/API management endpoints."""
    auth_enabled, password = await dashboard_auth_config()
    if not auth_enabled:
        return True
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {password}":
        return True
    cookie = request.cookies.get("ai_router_token", "")
    if cookie == password:
        return True
    raise HTTPException(status_code=401, detail="Unauthorized")


async def check_proxy_auth(request: Request):
    """
    Check auth for proxy endpoints (/v1/*).
    1. If require_api_key setting is true: must provide a valid local API key
    2. If require_api_key is false: allow any request through
    3. Dashboard auth (AUTH_PASSWORD) also works as fallback
    """
    require_api_key = await db.get_setting("require_api_key")
    if require_api_key != "true":
        return None  # No auth needed

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing API key. Use: Authorization: Bearer <your-key>")

    token = auth[7:].strip()
    
    # Check local API key
    local_key = await db.get_local_key(token)
    if local_key:
        return local_key["id"]

    # Fallback: dashboard password also works
    auth_enabled, dashboard_password = await dashboard_auth_config()
    if auth_enabled and token == dashboard_password:
        return None

    raise HTTPException(status_code=401, detail="Invalid API key")


@app.middleware("http")
async def protect_management_api(request: Request, call_next):
    """Apply dashboard auth to management API routes when enabled."""
    if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/auth/") and request.method != "OPTIONS":
        try:
            await check_dashboard_auth(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.on_event("startup")
async def startup():
    await db.get_db()


@app.on_event("shutdown")
async def shutdown():
    await db.close_db()


# ============ PROXY ENDPOINTS (OpenAI-compatible) ============

@app.get("/v1/health")
async def v1_health():
    return {"ok": True, "service": "ai-router", "ts": int(time.time())}

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    local_key_id = await check_proxy_auth(request)
    body = await request.json()
    result, status = await proxy.proxy_chat_completions(body, dict(request.headers), local_key_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(result, status_code=status)
    if hasattr(result, '__call__'):  # SSE response
        return result
    return JSONResponse(result, status_code=status)


@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    local_key_id = await check_proxy_auth(request)
    body = await request.json()
    result, status = await proxy.proxy_anthropic_messages(body, dict(request.headers), local_key_id)
    if isinstance(result, dict) and "error" in result:
        return JSONResponse(result, status_code=status)
    if hasattr(result, "__call__"):
        return result
    return JSONResponse(result, status_code=status)


@app.get("/v1/models")
async def list_models(request: Request):
    await check_proxy_auth(request)
    result, status = await proxy.proxy_models()
    return JSONResponse(result, status_code=status)


@app.post("/v1/models")
async def list_models_post(request: Request):
    await check_proxy_auth(request)
    result, status = await proxy.proxy_models()
    return JSONResponse(result, status_code=status)


# ============ MANAGEMENT API ============

@app.get("/api/health")
async def api_health():
    auth_enabled, _ = await dashboard_auth_config()
    return {"ok": True, "service": "ai-router", "auth_enabled": auth_enabled, "ts": int(time.time())}


@app.get("/api/auth/status")
async def api_auth_status(request: Request):
    auth_enabled, _ = await dashboard_auth_config()
    if not auth_enabled:
        return {"auth_enabled": False, "authenticated": True}
    try:
        await check_dashboard_auth(request)
        return {"auth_enabled": True, "authenticated": True}
    except HTTPException:
        return {"auth_enabled": True, "authenticated": False}


@app.post("/api/auth/login")
async def api_auth_login(request: Request):
    data = await request.json()
    password = data.get("password", "")
    auth_enabled, dashboard_password = await dashboard_auth_config()
    if not auth_enabled:
        return {"ok": True, "auth_enabled": False}
    if password != dashboard_password:
        raise HTTPException(status_code=401, detail="Invalid password")
    response = JSONResponse({"ok": True, "auth_enabled": True})
    response.set_cookie(
        "ai_router_token",
        dashboard_password,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.post("/api/auth/logout")
async def api_auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("ai_router_token")
    return response

# --- Providers ---

@app.get("/api/providers")
async def api_list_providers():
    return await db.list_providers()


@app.post("/api/providers")
async def api_create_provider(request: Request):
    data = await request.json()
    required = ["name", "base_url", "type"]
    for r in required:
        if not data.get(r):
            raise HTTPException(400, f"Missing field: {r}")
    try:
        provider = await db.create_provider(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    proxy.invalidate_models_cache()
    # Auto-discover models from upstream if keys provided
    models_to_add = data.get("models", [])
    if models_to_add:
        effective_prefix = ""
        if provider.get("prefix_enabled", 0):
            effective_prefix = provider.get("prefix", "")
        for m in models_to_add:
            alias = f"{effective_prefix}/{m}" if effective_prefix else m
            await db.add_alias(alias, provider["id"], m)
    return provider


@app.get("/api/providers/presets")
async def api_get_provider_presets():
 return db.get_provider_presets()


@app.get("/api/providers/{provider_id}")
async def api_get_provider(provider_id: str):
    p = await db.get_provider(provider_id)
    if not p:
        raise HTTPException(404, "Provider not found")
    return p


@app.put("/api/providers/{provider_id}")
async def api_update_provider(provider_id: str, request: Request):
    data = await request.json()
    try:
        p = await db.update_provider(provider_id, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    proxy.invalidate_models_cache(provider_id)
    if not p:
        raise HTTPException(404, "Provider not found")
    return p


@app.delete("/api/providers/{provider_id}")
async def api_delete_provider(provider_id: str):
    await db.delete_provider(provider_id)
    proxy.invalidate_models_cache(provider_id)
    return {"ok": True}


@app.post("/api/providers/{provider_id}/test")
async def api_test_provider(provider_id: str):
    return await proxy.test_provider(provider_id)


@app.post("/api/providers/{provider_id}/test-tools")
async def api_test_provider_tools(provider_id: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}

    model_id = data.get("model_id") or data.get("model")
    result = await proxy.test_provider_tools(provider_id, model_id)

    if result.get("valid"):
        await db.update_provider(provider_id, {"supports_tools": 1 if result.get("agent_ready") else 0})
        proxy.invalidate_models_cache(provider_id)

    return result


@app.post("/api/providers/{provider_id}/fetch-models")
async def api_fetch_models(provider_id: str):
    """Fetch available models from upstream provider and auto-register as aliases."""
    provider = await db.get_provider(provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")
    key = await db.get_alive_key(provider_id)
    if not key:
        raise HTTPException(400, "No alive keys - add a key first")
    result = await proxy.fetch_upstream_models(provider, key)
    effective_prefix = ""
    if provider.get("prefix_enabled", 0):
        effective_prefix = provider.get("prefix", "")
    added = []
    for m in result:
        alias = f"{effective_prefix}/{m}" if effective_prefix else m
        await db.add_alias(alias, provider_id, m)
        added.append(alias)
    return {"fetched": len(result), "added": added, "models": result}


@app.get("/api/providers/{provider_id}/models")
async def api_provider_models(provider_id: str):
    """List models for one provider without registering aliases."""
    result, status = await proxy.proxy_models(provider_id)
    return JSONResponse(result, status_code=status)


@app.delete("/api/providers/{provider_id}/models")
async def api_delete_provider_models(provider_id: str):
    provider = await db.get_provider(provider_id)
    if not provider:
        raise HTTPException(404, "Provider not found")
    deleted = await db.delete_aliases_for_provider(provider_id)
    proxy.invalidate_models_cache(provider_id)
    return {"ok": True, "deleted": deleted}


# --- Keys ---

@app.get("/api/keys")
async def api_list_keys(provider_id: str = None, status: str = None):
    return await db.list_keys(provider_id, status)


@app.post("/api/keys")
async def api_add_key(request: Request):
    data = await request.json()
    if not data.get("provider_id") or not data.get("key"):
        raise HTTPException(400, "Missing provider_id or key")
    key_id = await db.add_key(data["provider_id"], data["key"], data.get("label", ""))
    proxy.invalidate_models_cache(data.get("provider_id"))
    return {"id": key_id}


@app.post("/api/keys/bulk")
async def api_add_keys_bulk(request: Request):
    data = await request.json()
    if not data.get("provider_id") or not data.get("keys"):
        raise HTTPException(400, "Missing provider_id or keys")
    ids = await db.add_keys_bulk(data["provider_id"], data["keys"])
    proxy.invalidate_models_cache(data.get("provider_id"))
    return {"added": len(ids), "ids": ids}


@app.put("/api/keys/{key_id}")
async def api_update_key(key_id: str, request: Request):
    data = await request.json()
    await db.update_key(key_id, data)
    return {"ok": True}


@app.delete("/api/keys/{key_id}")
async def api_delete_key(key_id: str):
    await db.delete_key(key_id)
    proxy.invalidate_models_cache()
    return {"ok": True}


@app.post("/api/keys/{key_id}/activate")
async def api_activate_key(key_id: str):
    await db.update_key(key_id, {"status": "alive", "cooldown_until": None, "error_code": None, "last_error": None})
    proxy.invalidate_models_cache()
    return {"ok": True}


@app.post("/api/keys/{key_id}/deactivate")
async def api_deactivate_key(key_id: str):
    await db.update_key(key_id, {"status": "dead"})
    proxy.invalidate_models_cache()
    return {"ok": True}


# --- Local API Keys (our own keys for external users) ---

@app.get("/api/local-keys")
async def api_list_local_keys():
    return await db.list_local_keys()


@app.post("/api/local-keys")
async def api_create_local_key(request: Request):
    data = await request.json()
    name = data.get("name", "")
    key_value = data.get("key", None)  # Optional: provide custom key
    result = await db.create_local_key(name, key_value)
    return result


@app.delete("/api/local-keys/{key_id}")
async def api_delete_local_key(key_id: str):
    await db.delete_local_key(key_id)
    return {"ok": True}


@app.post("/api/local-keys/{key_id}/toggle")
async def api_toggle_local_key(key_id: str, request: Request):
    data = await request.json()
    await db.toggle_local_key(key_id, data.get("is_active", 1))
    return {"ok": True}


# --- Aliases / Models ---

@app.get("/api/aliases")
async def api_list_aliases(provider_id: str = None):
    return await db.list_aliases(provider_id)


@app.post("/api/aliases")
async def api_add_alias(request: Request):
    data = await request.json()
    if not data.get("alias") or not data.get("provider_id") or not data.get("model_id"):
        raise HTTPException(400, "Missing alias, provider_id, or model_id")
    await db.add_alias(data["alias"], data["provider_id"], data["model_id"], data.get("is_active", 1))
    return {"ok": True}


@app.post("/api/aliases/delete")
async def api_delete_alias(request: Request):
    data = await request.json()
    await db.delete_alias(data["alias"])
    return {"ok": True}


@app.post("/api/aliases/activate")
async def api_activate_alias(request: Request):
    data = await request.json()
    await db.toggle_alias(data["alias"], 1)
    return {"ok": True}


@app.post("/api/aliases/deactivate")
async def api_deactivate_alias(request: Request):
    data = await request.json()
    await db.toggle_alias(data["alias"], 0)
    return {"ok": True}


# --- Logs ---

@app.get("/api/logs")
async def api_get_logs(limit: int = 100, provider_id: str = None):
    return await db.get_logs(limit, provider_id)


# --- Stats ---

@app.get("/api/stats")
async def api_get_stats():
    return await db.get_stats()


# --- Settings ---

@app.get("/api/settings")
async def api_get_settings():
    return await db.get_all_settings()


@app.put("/api/settings")
async def api_set_settings(request: Request):
    data = await request.json()
    for k, v in data.items():
        await db.set_setting(k, str(v))
    return {"ok": True}


# --- Local API Keys (update) ---

@app.put("/api/local-keys/{key_id}")
async def api_update_local_key(key_id: str, request: Request):
    data = await request.json()
    result = await db.update_local_key(key_id, data)
    if not result:
        raise HTTPException(404, "Local key not found")
    return result


# --- Combos CRUD ---

@app.get("/api/combos")
async def api_list_combos():
    return await db.list_combos()


@app.post("/api/combos")
async def api_create_combo(request: Request):
    data = await request.json()
    try:
        return await db.create_combo(data)
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            raise HTTPException(409, f"Combo name '{data.get('name')}' already exists")
        raise HTTPException(400, str(e))


@app.get("/api/combos/{combo_id}")
async def api_get_combo(combo_id: str):
    combo = await db.get_combo(combo_id)
    if not combo:
        raise HTTPException(404, "Combo not found")
    return combo


@app.put("/api/combos/{combo_id}")
async def api_update_combo(combo_id: str, request: Request):
    data = await request.json()
    result = await db.update_combo(combo_id, data)
    if not result:
        raise HTTPException(404, "Combo not found")
    return result


@app.delete("/api/combos/{combo_id}")
async def api_delete_combo(combo_id: str):
    await db.delete_combo(combo_id)
    return {"ok": True}


@app.post("/api/combos/{combo_id}/models")
async def api_add_combo_model(combo_id: str, request: Request):
    data = await request.json()
    provider_id = data.get("provider_id")
    model_id = data.get("model_id")
    alias = data.get("alias", "")
    sort_order = data.get("sort_order", 0)
    if not provider_id or not model_id:
        raise HTTPException(400, "Missing provider_id or model_id")
    return await db.add_combo_model(combo_id, provider_id, model_id, alias, sort_order)


@app.delete("/api/combos/{combo_id}/models/{model_id}")
async def api_remove_combo_model(combo_id: str, model_id: str):
    await db.remove_combo_model(model_id)
    return {"ok": True}


@app.put("/api/combos/{combo_id}/models/{model_id}")
async def api_update_combo_model(combo_id: str, model_id: str, request: Request):
    data = await request.json()
    await db.update_combo_model(model_id, data)
    return {"ok": True}


# ============ STATIC FILES (React frontend) ============

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/{path:path}")
    async def serve_spa(path: str):
        file_path = os.path.abspath(os.path.join(STATIC_DIR, path))
        if file_path.startswith(os.path.abspath(STATIC_DIR) + os.sep) and os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
