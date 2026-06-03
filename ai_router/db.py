import aiosqlite
import os
import json
import logging
import uuid
import re
from . import config

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'openai-compatible',
  base_url TEXT NOT NULL,
  prefix TEXT DEFAULT '',
  prefix_enabled INTEGER DEFAULT 0,
  api_type TEXT DEFAULT 'chat',
  is_active INTEGER DEFAULT 1,
  supports_tools INTEGER DEFAULT 1,
  supports_streaming INTEGER DEFAULT 1,
  supports_json_mode INTEGER DEFAULT 1,
  extra_headers TEXT DEFAULT '{}',
  auth_type TEXT DEFAULT '',
  auth_header TEXT DEFAULT '',
  auth_prefix TEXT DEFAULT '',
  key_query_param TEXT DEFAULT '',
  chat_path TEXT DEFAULT '',
  models_path TEXT DEFAULT '',
  request_format TEXT DEFAULT '',
  anthropic_version TEXT DEFAULT '2023-06-01',
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  key_value TEXT NOT NULL,
  label TEXT DEFAULT '',
  status TEXT DEFAULT 'alive',
  last_used TEXT,
  last_error TEXT,
  error_code INTEGER,
  cooldown_until TEXT,
  total_requests INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS key_model_locks (
  key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  locked_until TEXT NOT NULL,
  error_code INTEGER,
  error TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  PRIMARY KEY (key_id, model)
);

CREATE TABLE IF NOT EXISTS model_aliases (
  alias TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  model_id TEXT NOT NULL,
  is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS local_api_keys (
  id TEXT PRIMARY KEY,
  key_value TEXT NOT NULL UNIQUE,
  name TEXT DEFAULT '',
  is_active INTEGER DEFAULT 1,
  total_requests INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  rate_limit INTEGER DEFAULT 0,
  last_used TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS combos (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT DEFAULT '',
  mode TEXT DEFAULT 'round_robin',
  is_active INTEGER DEFAULT 1,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS combo_models (
 id TEXT PRIMARY KEY,
 combo_id TEXT NOT NULL REFERENCES combos(id) ON DELETE CASCADE,
 provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
 model_id TEXT NOT NULL,
 alias TEXT DEFAULT '',
 is_active INTEGER DEFAULT 1,
 sort_order INTEGER DEFAULT 0,
 created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS request_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id TEXT,
  key_id TEXT,
  local_key_id TEXT,
  model TEXT,
  tokens_in INTEGER DEFAULT 0,
  tokens_out INTEGER DEFAULT 0,
  latency_ms INTEGER DEFAULT 0,
  status_code INTEGER,
  error TEXT,
  fallback_chain TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

INSERT OR IGNORE INTO settings (key, value) VALUES ('strategy', 'round-robin');
INSERT OR IGNORE INTO settings (key, value) VALUES ('require_login', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('login_password', 'ABC12345');
INSERT OR IGNORE INTO settings (key, value) VALUES ('require_api_key', 'true');
INSERT OR IGNORE INTO settings (key, value) VALUES ('rtk_enabled', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('rr_counter', '0');
"""

PROVIDER_PRESETS = [
    {"name": "Custom OpenAI Compatible", "type": "openai-compatible", "base_url": "https://example.com/v1", "prefix": "custom", "prefix_enabled": 0, "auth_type": "bearer", "chat_path": "/chat/completions", "models_path": "/models"},
    {"name": "Custom Claude / Anthropic Compatible", "type": "anthropic-compatible", "base_url": "https://example.com/v1", "prefix": "custom-claude", "prefix_enabled": 0, "auth_type": "x-api-key", "chat_path": "/messages", "anthropic_version": "2023-06-01"},
    {"name": "Claude CLI Provider", "type": "claude-cli", "base_url": "https://api.anthropic.com/v1", "prefix": "claude-cli", "prefix_enabled": 0, "auth_type": "x-api-key", "chat_path": "/messages", "models_path": "", "request_format": "anthropic-compatible", "supports_tools": 1, "supports_streaming": 1, "supports_json_mode": 1, "anthropic_version": "2023-06-01"},
    {"name": "OpenAI", "type": "openai-compatible", "base_url": "https://api.openai.com/v1", "prefix": "openai", "prefix_enabled": 0},
    {"name": "Anthropic (Claude)", "type": "anthropic-compatible", "base_url": "https://api.anthropic.com/v1", "prefix": "claude", "prefix_enabled": 0, "auth_type": "x-api-key", "chat_path": "/messages"},
    {"name": "NVIDIA NIM", "type": "openai-compatible", "base_url": "https://integrate.api.nvidia.com/v1", "prefix": "nvidia", "prefix_enabled": 0},
    {"name": "Groq", "type": "openai-compatible", "base_url": "https://api.groq.com/openai/v1", "prefix": "groq", "prefix_enabled": 0},
    {"name": "OpenRouter", "type": "openai-compatible", "base_url": "https://openrouter.ai/api/v1", "prefix": "openrouter", "prefix_enabled": 0},
    {"name": "Mimo", "type": "openai-compatible", "base_url": "https://api.mimo.company/v1", "prefix": "mimo", "prefix_enabled": 0},
 {"name": "NovAI", "type": "openai-compatible", "base_url": "https://aiapi-pro.com/v1", "prefix": "novai", "prefix_enabled": 0},
 {"name": "Google Gemini", "type": "openai-compatible", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "prefix": "gemini", "prefix_enabled": 0},
    {"name": "DeepSeek", "type": "openai-compatible", "base_url": "https://api.deepseek.com/v1", "prefix": "deepseek", "prefix_enabled": 0},
    {"name": "Together AI", "type": "openai-compatible", "base_url": "https://api.together.xyz/v1", "prefix": "together", "prefix_enabled": 0},
    {"name": "Fireworks AI", "type": "openai-compatible", "base_url": "https://api.fireworks.ai/inference/v1", "prefix": "fireworks", "prefix_enabled": 0},
]

_db = None


async def get_db() -> aiosqlite.Connection:
    global _db
    if _db is None:
        os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
        _db = await aiosqlite.connect(config.DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _db.executescript(SCHEMA)
        # Safe migrations for existing DBs
        for mig in [
            "ALTER TABLE providers ADD COLUMN prefix_enabled INTEGER DEFAULT 0",
            "ALTER TABLE providers ADD COLUMN supports_tools INTEGER DEFAULT 1",
            "ALTER TABLE providers ADD COLUMN supports_streaming INTEGER DEFAULT 1",
            "ALTER TABLE providers ADD COLUMN supports_json_mode INTEGER DEFAULT 1",
            "ALTER TABLE providers ADD COLUMN auth_type TEXT DEFAULT ''",
            "ALTER TABLE providers ADD COLUMN auth_header TEXT DEFAULT ''",
            "ALTER TABLE providers ADD COLUMN auth_prefix TEXT DEFAULT ''",
            "ALTER TABLE providers ADD COLUMN key_query_param TEXT DEFAULT ''",
            "ALTER TABLE providers ADD COLUMN chat_path TEXT DEFAULT ''",
            "ALTER TABLE providers ADD COLUMN models_path TEXT DEFAULT ''",
            "ALTER TABLE providers ADD COLUMN request_format TEXT DEFAULT ''",
            "ALTER TABLE providers ADD COLUMN anthropic_version TEXT DEFAULT '2023-06-01'",
            """CREATE TABLE IF NOT EXISTS key_model_locks (
              key_id TEXT NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
              model TEXT NOT NULL,
              locked_until TEXT NOT NULL,
              error_code INTEGER,
              error TEXT,
              created_at TEXT DEFAULT (datetime('now')),
              PRIMARY KEY (key_id, model)
            )""",
            "ALTER TABLE request_logs ADD COLUMN local_key_id TEXT",
            "ALTER TABLE request_logs ADD COLUMN fallback_chain TEXT",
            "ALTER TABLE combos ADD COLUMN mode TEXT DEFAULT 'round_robin'",
            "ALTER TABLE combo_models ADD COLUMN alias TEXT DEFAULT ''",
            "ALTER TABLE combo_models ADD COLUMN sort_order INTEGER DEFAULT 0",
            "ALTER TABLE combo_models ADD COLUMN created_at TEXT DEFAULT (datetime('now'))",
        ]:
            try:
                await _db.execute(mig)
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning("Migration skipped/failed: %s: %s", mig, e)
        if config.AUTH_PASSWORD:
            await _db.execute(
                "UPDATE settings SET value=? WHERE key='login_password' AND (value IS NULL OR value='')",
                (config.AUTH_PASSWORD,),
            )
        await _db.execute("UPDATE providers SET supports_tools=1, supports_streaming=1 WHERE type='claude-cli'")
        await _db.commit()
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


def get_provider_presets():
    """Return list of provider presets (templates for user to add)."""
    return PROVIDER_PRESETS


def _normalize_provider_data(data: dict, existing: dict | None = None) -> dict:
    """Apply sane defaults for custom OpenAI/Anthropic-compatible providers."""
    merged = dict(existing or {})
    merged.update(data or {})

    provider_type = (merged.get("type") or "openai-compatible").strip()
    if provider_type not in ("openai-compatible", "anthropic-compatible", "claude-cli"):
        raise ValueError("Provider type must be openai-compatible, anthropic-compatible, or claude-cli")
    merged["type"] = provider_type

    request_format = (merged.get("request_format") or "").strip()
    if request_format and request_format not in ("openai-compatible", "anthropic-compatible"):
        raise ValueError("Request format must be openai-compatible or anthropic-compatible")
    if provider_type == "claude-cli":
        request_format = "anthropic-compatible"
    merged["request_format"] = request_format

    effective_format = request_format or ("anthropic-compatible" if provider_type == "claude-cli" else provider_type)
    merged["base_url"] = (merged.get("base_url") or "").strip().rstrip("/")
    merged["prefix"] = (merged.get("prefix") or "").strip()
    merged["prefix_enabled"] = int(merged.get("prefix_enabled", 0))
    merged["api_type"] = merged.get("api_type") or "chat"
    merged["supports_tools"] = int(merged.get("supports_tools", 1))
    merged["supports_streaming"] = int(merged.get("supports_streaming", 1))
    merged["supports_json_mode"] = int(merged.get("supports_json_mode", 1))
    if provider_type == "claude-cli":
        merged["supports_tools"] = 1
        merged["supports_streaming"] = 1
    merged["anthropic_version"] = merged.get("anthropic_version") or "2023-06-01"
    extra_headers = merged.get("extra_headers") or {}
    if isinstance(extra_headers, str):
        try:
            extra_headers = json.loads(extra_headers) if extra_headers.strip() else {}
        except Exception:
            extra_headers = {}
    merged["extra_headers"] = extra_headers if isinstance(extra_headers, dict) else {}

    if effective_format == "anthropic-compatible":
        merged["auth_type"] = merged.get("auth_type") or "x-api-key"
        merged["chat_path"] = merged.get("chat_path") or "/messages"
        merged["models_path"] = merged.get("models_path") or ""
    else:
        merged["auth_type"] = merged.get("auth_type") or "bearer"
        merged["chat_path"] = merged.get("chat_path") or "/chat/completions"
        merged["models_path"] = merged.get("models_path") or "/models"

    return merged


async def list_providers():
    db = await get_db()
    cursor = await db.execute("SELECT * FROM providers ORDER BY created_at DESC")
    rows = await cursor.fetchall()
    result = []
    for r in rows:
        provider = dict(r)
        cur2 = await db.execute("SELECT status, COUNT(*) as cnt FROM api_keys WHERE provider_id=? GROUP BY status", (provider["id"],))
        key_stats = {}
        for kr in await cur2.fetchall():
            key_stats[kr["status"]] = kr["cnt"]
        provider["key_stats"] = key_stats
        cur3 = await db.execute("SELECT alias, model_id, is_active FROM model_aliases WHERE provider_id=?", (provider["id"],))
        provider["aliases"] = [dict(a) for a in await cur3.fetchall()]
        result.append(provider)
    return result


async def get_provider(provider_id: str):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM providers WHERE id=?", (provider_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    provider = dict(row)
    cur2 = await db.execute("SELECT * FROM api_keys WHERE provider_id=? ORDER BY created_at", (provider_id,))
    provider["keys"] = [dict(k) for k in await cur2.fetchall()]
    cur3 = await db.execute("SELECT alias, model_id, is_active FROM model_aliases WHERE provider_id=?", (provider_id,))
    provider["aliases"] = [dict(a) for a in await cur3.fetchall()]
    return provider


async def create_provider(data: dict):
    db = await get_db()
    data = _normalize_provider_data(data)
    provider_id = data.get("id") or f"{data.get('type', 'openai-compatible')}-{uuid.uuid4()}"
    prefix = data.get("prefix", "").strip()
    prefix_enabled = 0
    if "prefix_enabled" in data:
        prefix_enabled = int(data["prefix_enabled"])
    await db.execute(
        "INSERT INTO providers (id, name, type, base_url, prefix, prefix_enabled, api_type, is_active, supports_tools, supports_streaming, supports_json_mode, extra_headers, auth_type, auth_header, auth_prefix, key_query_param, chat_path, models_path, request_format, anthropic_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (provider_id, data["name"], data.get("type", "openai-compatible"), data["base_url"],
         prefix, prefix_enabled, data.get("api_type", "chat"), 1,
         int(data.get("supports_tools", 1)), int(data.get("supports_streaming", 1)), int(data.get("supports_json_mode", 1)),
         json.dumps(data.get("extra_headers", {})),
         data.get("auth_type", ""), data.get("auth_header", ""), data.get("auth_prefix", ""),
         data.get("key_query_param", ""), data.get("chat_path", ""), data.get("models_path", ""),
         data.get("request_format", ""), data.get("anthropic_version", "2023-06-01"))
    )
    await db.commit()
    return await get_provider(provider_id)


async def update_provider(provider_id: str, data: dict):
    db = await get_db()
    current = await get_provider(provider_id)
    if not current:
        return None
    requested_fields = set((data or {}).keys())
    normalized = _normalize_provider_data(data, current)
    if "type" in requested_fields or "request_format" in requested_fields:
        requested_fields.update(("auth_type", "chat_path", "models_path", "anthropic_version"))
    sets = []
    vals = []
    for field in ("name", "type", "base_url", "prefix", "prefix_enabled", "api_type", "is_active", "supports_tools", "supports_streaming", "supports_json_mode", "extra_headers", "auth_type", "auth_header", "auth_prefix", "key_query_param", "chat_path", "models_path", "request_format", "anthropic_version"):
        if field in requested_fields:
            val = normalized[field]
            if field == "extra_headers":
                val = val if isinstance(val, str) else json.dumps(val)
            sets.append(f"{field}=?")
            vals.append(val)
    if not sets:
        return await get_provider(provider_id)
    vals.append(provider_id)
    await db.execute(f"UPDATE providers SET {','.join(sets)} WHERE id=?", vals)
    await db.commit()
    return await get_provider(provider_id)


async def delete_provider(provider_id: str):
    db = await get_db()
    await db.execute("DELETE FROM model_aliases WHERE provider_id=?", (provider_id,))
    await db.execute("DELETE FROM api_keys WHERE provider_id=?", (provider_id,))
    await db.execute("DELETE FROM combo_models WHERE provider_id=?", (provider_id,))
    await db.execute("DELETE FROM providers WHERE id=?", (provider_id,))
    await db.commit()


AUTO_KEY_NAME_RE = re.compile(r"^apikey-(\d+)$")


async def _next_auto_key_label(db, provider_id: str = None, table: str = "api_keys") -> str:
    if table == "local_api_keys":
        cursor = await db.execute("SELECT name AS value FROM local_api_keys WHERE name LIKE 'apikey-%'")
    else:
        cursor = await db.execute(
            "SELECT label AS value FROM api_keys WHERE provider_id=? AND label LIKE 'apikey-%'",
            (provider_id,)
        )
    rows = await cursor.fetchall()
    highest = 0
    for row in rows:
        match = AUTO_KEY_NAME_RE.match((row["value"] or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"apikey-{highest + 1}"

async def list_keys(provider_id: str = None, status: str = None):
    db = await get_db()
    q = "SELECT * FROM api_keys"
    conds = []
    vals = []
    if provider_id:
        conds.append("provider_id=?")
        vals.append(provider_id)
    if status:
        conds.append("status=?")
        vals.append(status)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY created_at"
    cursor = await db.execute(q, vals)
    return [dict(r) for r in await cursor.fetchall()]


async def add_key(provider_id: str, key_value: str, label: str = ""):
    db = await get_db()
    key_id = str(uuid.uuid4())
    label = (label or "").strip()
    if not label:
        label = await _next_auto_key_label(db, provider_id)
    await db.execute(
        "INSERT INTO api_keys (id, provider_id, key_value, label, status) VALUES (?,?,?,?,?)",
        (key_id, provider_id, key_value, label, "alive")
    )
    await db.commit()
    return key_id


async def add_keys_bulk(provider_id: str, keys: list):
    db = await get_db()
    ids = []
    next_auto_num = None
    for k in keys:
        key_val = k if isinstance(k, str) else k.get("key", k.get("api_key", ""))
        label = (k.get("label", "") if isinstance(k, dict) else "").strip()
        if not key_val:
            continue
        if not label:
            if next_auto_num is None:
                first_label = await _next_auto_key_label(db, provider_id)
                next_auto_num = int(first_label.rsplit("-", 1)[1])
            label = f"apikey-{next_auto_num}"
            next_auto_num += 1
        key_id = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO api_keys (id, provider_id, key_value, label, status) VALUES (?,?,?,?,?)",
            (key_id, provider_id, key_val, label, "alive")
        )
        ids.append(key_id)
    await db.commit()
    return ids


async def update_key(key_id: str, data: dict):
    db = await get_db()
    sets = []
    vals = []
    for field in ("label", "status", "key_value", "cooldown_until", "last_error", "error_code", "last_used", "total_requests", "total_tokens"):
        if field in data:
            sets.append(f"{field}=?")
            vals.append(data[field])
    if not sets:
        return
    vals.append(key_id)
    await db.execute(f"UPDATE api_keys SET {','.join(sets)} WHERE id=?", vals)
    await db.commit()


async def delete_key(key_id: str):
    db = await get_db()
    await db.execute("DELETE FROM api_keys WHERE id=?", (key_id,))
    await db.commit()


async def get_alive_key(provider_id: str, model: str = None):
    keys = await list_alive_keys(provider_id, model=model)
    return keys[0] if keys else None


async def has_alive_key(provider_id: str, model: str = None) -> bool:
    keys = await list_alive_keys(provider_id, advance=False, model=model)
    return bool(keys)


async def list_alive_keys(provider_id: str, advance: bool = True, model: str = None):
    """Return all alive keys for a provider, rotated from the current RR position."""
    db = await get_db()
    now = datetime_utc_now()
    await db.execute(
        "UPDATE api_keys SET status='alive', cooldown_until=NULL WHERE provider_id=? AND status='cooldown' AND cooldown_until IS NOT NULL AND cooldown_until < ?",
        (provider_id, now)
    )
    await db.execute("DELETE FROM key_model_locks WHERE locked_until < ?", (now,))
    await db.commit()
    if model:
        cursor = await db.execute(
            """
            SELECT k.* FROM api_keys k
            WHERE k.provider_id=? AND k.status='alive'
              AND NOT EXISTS (
                SELECT 1 FROM key_model_locks l
                WHERE l.key_id=k.id AND LOWER(l.model)=LOWER(?) AND l.locked_until >= ?
              )
            ORDER BY k.last_used ASC NULLS FIRST
            """,
            (provider_id, model, now)
        )
    else:
        cursor = await db.execute(
            "SELECT * FROM api_keys WHERE provider_id=? AND status='alive' ORDER BY last_used ASC NULLS FIRST",
            (provider_id,)
        )
    keys = [dict(r) for r in await cursor.fetchall()]
    if not keys:
        return []
    rr = await _get_rr(f"key_rr_{provider_id}")
    start = rr % len(keys)
    ordered = keys[start:] + keys[:start]
    if advance:
        await _set_rr(f"key_rr_{provider_id}", (rr + 1) % len(keys))
    return ordered


async def mark_key_used(key_id: str):
    db = await get_db()
    now = datetime_utc_now()
    await db.execute("UPDATE api_keys SET last_used=?, total_requests=total_requests+1 WHERE id=?", (now, key_id))
    await db.commit()


def _model_lock_seconds(error_code: int, error_msg: str = ""):
    if error_code in (429, 503, 529):
        return 60
    if error_code in (408, 500, 502, 504):
        return 30
    if error_code in (400, 404):
        return 300
    lowered = (error_msg or "").lower()
    if "model" in lowered or "not found" in lowered or "unsupported" in lowered or "function" in lowered:
        return 300
    return 30


def _global_key_dead(error_code: int, error_msg: str = ""):
    if error_code not in (401, 403):
        return False
    lowered = (error_msg or "").lower()
    return not ("model" in lowered or "not found" in lowered or "unsupported" in lowered or "function" in lowered)


async def lock_key_model(key_id: str, model: str, error_code: int, error_msg: str, seconds: int = None):
    if not model:
        return
    db = await get_db()
    from datetime import datetime, timedelta
    locked_until = (datetime.utcnow() + timedelta(seconds=seconds or _model_lock_seconds(error_code, error_msg))).isoformat()
    await db.execute(
        "INSERT OR REPLACE INTO key_model_locks (key_id, model, locked_until, error_code, error) VALUES (?,?,?,?,?)",
        (key_id, model, locked_until, error_code, (error_msg or "")[:500])
    )
    await db.commit()


async def mark_key_error(key_id: str, error_code: int, error_msg: str, model: str = None):
    db = await get_db()
    status = "alive"
    cooldown_until = None
    if _global_key_dead(error_code, error_msg):
        status = "dead"

    await db.execute(
        "UPDATE api_keys SET status=?, error_code=?, last_error=?, cooldown_until=? WHERE id=?",
        (status, error_code, error_msg, cooldown_until, key_id)
    )
    await db.commit()
    if status != "dead" and model:
        await lock_key_model(key_id, model, error_code, error_msg)
    return status


async def mark_key_success(key_id: str, tokens_in: int = 0, tokens_out: int = 0):
    db = await get_db()
    now = datetime_utc_now()
    await db.execute(
        "UPDATE api_keys SET status='alive', last_used=?, total_tokens=total_tokens+?, error_code=NULL, last_error=NULL, cooldown_until=NULL WHERE id=?",
        (now, tokens_in + tokens_out, key_id)
    )
    await db.commit()


async def clear_key_model_lock(key_id: str, model: str):
    if not model:
        return
    db = await get_db()
    await db.execute("DELETE FROM key_model_locks WHERE key_id=? AND LOWER(model)=LOWER(?)", (key_id, model))
    await db.commit()


async def _get_rr(key: str) -> int:
    db = await get_db()
    cursor = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cursor.fetchone()
    return int(row["value"]) if row else 0


async def _set_rr(key: str, value: int):
    db = await get_db()
    await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, str(value)))
    await db.commit()


async def get_rr_counter() -> int:
    return await _get_rr("rr_counter")


async def increment_rr_counter() -> int:
    db = await get_db()
    cur = await _get_rr("rr_counter")
    nxt = cur + 1
    await _set_rr("rr_counter", nxt)
    return cur


async def list_local_keys():
    db = await get_db()
    cursor = await db.execute("SELECT * FROM local_api_keys ORDER BY created_at DESC")
    return [dict(r) for r in await cursor.fetchall()]


async def get_local_key(key_value: str):
    """Look up a local API key by value. Returns None if not found or disabled."""
    db = await get_db()
    cursor = await db.execute("SELECT * FROM local_api_keys WHERE key_value=? AND is_active=1", (key_value,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def create_local_key(name: str = "", key_value: str = None):
 """Create a local API key. If key_value is empty, auto-generate one."""
 if not key_value or not key_value.strip():
  key_value = f"ar-{uuid.uuid4().hex[:32]}"
 else:
  key_value = key_value.strip()
 db = await get_db()
 name = (name or "").strip()
 if not name:
  name = await _next_auto_key_label(db, table="local_api_keys")
 lid = str(uuid.uuid4())
 await db.execute(
  "INSERT OR IGNORE INTO local_api_keys (id, key_value, name, is_active) VALUES (?,?,?,1)",
  (lid, key_value, name)
 )
 await db.commit()
 cursor = await db.execute("SELECT * FROM local_api_keys WHERE id=?", (lid,))
 row = await cursor.fetchone()
 return dict(row) if row else None


async def delete_local_key(key_id: str):
    db = await get_db()
    await db.execute("DELETE FROM local_api_keys WHERE id=?", (key_id,))
    await db.commit()


async def toggle_local_key(key_id: str, is_active: int):
    db = await get_db()
    await db.execute("UPDATE local_api_keys SET is_active=? WHERE id=?", (is_active, key_id))
    await db.commit()


async def update_local_key(key_id: str, data: dict):
    db = await get_db()
    sets = []
    vals = []
    for field in ("name", "key_value", "is_active", "rate_limit"):
        if field in data:
            sets.append(f"{field}=?")
            vals.append(data[field])
    if not sets:
        return
    vals.append(key_id)
    await db.execute(f"UPDATE local_api_keys SET {','.join(sets)} WHERE id=?", vals)
    await db.commit()


async def mark_local_key_used(key_id: str, tokens: int = 0):
    db = await get_db()
    now = datetime_utc_now()
    await db.execute(
        "UPDATE local_api_keys SET last_used=?, total_requests=total_requests+1, total_tokens=total_tokens+? WHERE id=?",
        (now, tokens, key_id)
    )
    await db.commit()


async def list_aliases(provider_id: str = None):
    db = await get_db()
    if provider_id:
        cursor = await db.execute("SELECT a.*, p.name as provider_name FROM model_aliases a LEFT JOIN providers p ON a.provider_id=p.id WHERE a.provider_id=?", (provider_id,))
    else:
        cursor = await db.execute("SELECT a.*, p.name as provider_name FROM model_aliases a LEFT JOIN providers p ON a.provider_id=p.id")
    return [dict(r) for r in await cursor.fetchall()]


async def add_alias(alias: str, provider_id: str, model_id: str, is_active: int = 1):
    db = await get_db()
    await db.execute("INSERT OR REPLACE INTO model_aliases (alias, provider_id, model_id, is_active) VALUES (?,?,?,?)", (alias, provider_id, model_id, is_active))
    await db.commit()


async def delete_alias(alias: str):
    db = await get_db()
    await db.execute("DELETE FROM model_aliases WHERE alias=?", (alias,))
    await db.commit()


async def delete_aliases_for_provider(provider_id: str):
    db = await get_db()
    cursor = await db.execute("SELECT COUNT(*) as cnt FROM model_aliases WHERE provider_id=?", (provider_id,))
    row = await cursor.fetchone()
    deleted = row["cnt"] if row else 0
    await db.execute("DELETE FROM model_aliases WHERE provider_id=?", (provider_id,))
    await db.commit()
    return deleted


async def toggle_alias(alias: str, is_active: int):
    db = await get_db()
    await db.execute("UPDATE model_aliases SET is_active=? WHERE alias=?", (is_active, alias))
    await db.commit()


async def resolve_alias(alias: str):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM model_aliases WHERE LOWER(alias)=LOWER(?) AND is_active=1", (alias,))
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None


async def get_alias(alias: str):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM model_aliases WHERE LOWER(alias)=LOWER(?)", (alias,))
    row = await cursor.fetchone()
    if row:
        return dict(row)
    return None


async def list_combos():
    db = await get_db()
    cursor = await db.execute("""
        SELECT c.*, 
               (SELECT COUNT(*) FROM combo_models WHERE combo_id=c.id) as model_count
        FROM combos c ORDER BY c.created_at DESC
    """)
    combos = [dict(r) for r in await cursor.fetchall()]
    for combo in combos:
        cur2 = await db.execute("""
            SELECT cm.*, p.name as provider_name, p.type as provider_type, p.base_url as provider_base_url
            FROM combo_models cm 
            LEFT JOIN providers p ON cm.provider_id=p.id
            WHERE cm.combo_id=? ORDER BY cm.sort_order, cm.created_at
        """, (combo["id"],))
        combo["models"] = [dict(m) for m in await cur2.fetchall()]
    return combos


async def get_combo(combo_id: str):
    db = await get_db()
    cursor = await db.execute("SELECT * FROM combos WHERE id=?", (combo_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    combo = dict(row)
    cur2 = await db.execute("""
        SELECT cm.*, p.name as provider_name, p.type as provider_type, p.base_url as provider_base_url
        FROM combo_models cm 
        LEFT JOIN providers p ON cm.provider_id=p.id
        WHERE cm.combo_id=? ORDER BY cm.sort_order, cm.created_at
    """, (combo_id,))
    combo["models"] = [dict(m) for m in await cur2.fetchall()]
    return combo


async def create_combo(data: dict):
    db = await get_db()
    combo_id = data.get("id") or str(uuid.uuid4())
    name = data.get("name", "").strip()
    if not name:
        raise ValueError("Combo name is required")
    await db.execute(
        "INSERT INTO combos (id, name, description, is_active) VALUES (?,?,?,?)",
        (combo_id, name, data.get("description", ""), 1)
    )
    await db.commit()
    for m in data.get("models", []):
        mid = str(uuid.uuid4())
        await db.execute(
            "INSERT INTO combo_models (id, combo_id, provider_id, model_id, alias, is_active, sort_order) VALUES (?,?,?,?,?,?,?)",
            (mid, combo_id, m["provider_id"], m["model_id"], m.get("alias", ""), 1, m.get("sort_order", 0))
        )
    await db.commit()
    return await get_combo(combo_id)


async def update_combo(combo_id: str, data: dict):
    db = await get_db()
    sets = []
    vals = []
    for field in ("name", "description", "is_active", "mode"):
        if field in data:
            sets.append(f"{field}=?")
            vals.append(data[field])
    if not sets:
        return await get_combo(combo_id)
    vals.append(combo_id)
    await db.execute(f"UPDATE combos SET {','.join(sets)} WHERE id=?", vals)
    await db.commit()
    return await get_combo(combo_id)


async def delete_combo(combo_id: str):
    db = await get_db()
    await db.execute("DELETE FROM combo_models WHERE combo_id=?", (combo_id,))
    await db.execute("DELETE FROM combos WHERE id=?", (combo_id,))
    await db.commit()


async def add_combo_model(combo_id: str, provider_id: str, model_id: str, alias: str = "", sort_order: int = 0):
    db = await get_db()
    mid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO combo_models (id, combo_id, provider_id, model_id, alias, is_active, sort_order) VALUES (?,?,?,?,?,?,?)",
        (mid, combo_id, provider_id, model_id, alias, 1, sort_order)
    )
    await db.commit()
    return mid


async def remove_combo_model(model_id: str):
    db = await get_db()
    await db.execute("DELETE FROM combo_models WHERE id=?", (model_id,))
    await db.commit()


async def update_combo_model(model_id: str, data: dict):
    db = await get_db()
    sets = []
    vals = []
    for field in ("provider_id", "model_id", "alias", "is_active", "sort_order"):
        if field in data:
            sets.append(f"{field}=?")
            vals.append(data[field])
    if not sets:
        return
    vals.append(model_id)
    await db.execute(f"UPDATE combo_models SET {','.join(sets)} WHERE id=?", vals)
    await db.commit()


async def list_combo_models(combo_id: str):
    db = await get_db()
    cursor = await db.execute("""
        SELECT cm.*, p.name as provider_name, p.type as provider_type
        FROM combo_models cm 
        LEFT JOIN providers p ON cm.provider_id=p.id
        WHERE cm.combo_id=? ORDER BY cm.sort_order, cm.created_at
    """, (combo_id,))
    return [dict(r) for r in await cursor.fetchall()]


async def resolve_combo_model(combo_name: str):
    """Resolve a combo name to (provider_id, model_id) using round-robin among available models."""
    candidates = await resolve_combo_candidates(combo_name)
    if not candidates:
        return None, ""
    picked = candidates[0]
    return picked["provider_id"], picked["model_id"]


async def combo_exists(combo_name: str):
    db = await get_db()
    cursor = await db.execute("SELECT id, name, is_active FROM combos WHERE LOWER(name)=LOWER(?)", (combo_name,))
    row = await cursor.fetchone()
    return dict(row) if row else None


async def resolve_combo_candidates(combo_name: str):
    """Return combo model candidates in fallback order."""
    db = await get_db()
    cursor = await db.execute("SELECT * FROM combos WHERE LOWER(name)=LOWER(?) AND is_active=1", (combo_name,))
    row = await cursor.fetchone()
    if not row:
        return []
    combo = dict(row)
    combo_id = combo["id"]
    
    cursor = await db.execute("""
        SELECT cm.*, p.is_active as provider_active
        FROM combo_models cm
        LEFT JOIN providers p ON cm.provider_id=p.id
        WHERE cm.combo_id=? AND cm.is_active=1
        ORDER BY cm.sort_order, cm.created_at
    """, (combo_id,))
    models = [dict(r) for r in await cursor.fetchall()]
    
    if not models:
        return []
    
    available = []
    for m in models:
        if not m.get("provider_active", 0):
            continue
        if await has_alive_key(m["provider_id"], m["model_id"]):
            available.append(m)
    
    if not available:
        return []
    
    if combo.get("mode", "round_robin") == "single":
        return available

    rr = await _get_rr(f"combo_rr_{combo_id}")
    start = rr % len(available)
    ordered = available[start:] + available[:start]
    await _set_rr(f"combo_rr_{combo_id}", (rr + 1) % len(available))
    return ordered


async def add_log(provider_id: str, key_id: str, model: str, tokens_in: int, tokens_out: int, latency_ms: int, status_code: int, error: str = None, local_key_id: str = None, fallback_chain=None):
    db = await get_db()
    chain_value = json.dumps(fallback_chain) if isinstance(fallback_chain, (list, dict)) else fallback_chain
    await db.execute(
        "INSERT INTO request_logs (provider_id, key_id, local_key_id, model, tokens_in, tokens_out, latency_ms, status_code, error, fallback_chain) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (provider_id, key_id, local_key_id, model, tokens_in, tokens_out, latency_ms, status_code, error, chain_value)
    )
    await db.commit()


async def get_logs(limit: int = 100, provider_id: str = None):
    db = await get_db()
    q = "SELECT l.*, p.name as provider_name FROM request_logs l LEFT JOIN providers p ON l.provider_id=p.id"
    vals = []
    if provider_id:
        q += " WHERE l.provider_id=?"
        vals.append(provider_id)
    q += " ORDER BY l.id DESC LIMIT ?"
    vals.append(limit)
    cursor = await db.execute(q, vals)
    return [dict(r) for r in await cursor.fetchall()]


async def get_stats():
    db = await get_db()
    cursor = await db.execute("SELECT COUNT(*) as total FROM request_logs WHERE date(created_at) = date('now')")
    row = await cursor.fetchone()
    total_today = row["total"] if row else 0
    cursor = await db.execute("SELECT COALESCE(SUM(tokens_in + tokens_out), 0) as total FROM request_logs WHERE date(created_at) = date('now')")
    row = await cursor.fetchone()
    tokens_today = row["total"] if row else 0
    cursor = await db.execute("SELECT p.name, COUNT(l.id) as requests, COALESCE(SUM(l.tokens_in+l.tokens_out),0) as tokens FROM request_logs l LEFT JOIN providers p ON l.provider_id=p.id WHERE date(l.created_at)=date('now') GROUP BY l.provider_id")
    provider_stats = [dict(r) for r in await cursor.fetchall()]
    cursor = await db.execute("SELECT status, COUNT(*) as cnt FROM api_keys GROUP BY status")
    key_stats = {}
    for r in await cursor.fetchall():
        key_stats[r["status"]] = r["cnt"]
    cursor = await db.execute("SELECT COUNT(*) as cnt FROM providers WHERE is_active=1")
    row = await cursor.fetchone()
    active_providers = row["cnt"] if row else 0
    cursor = await db.execute("SELECT COUNT(*) as cnt FROM local_api_keys WHERE is_active=1")
    row = await cursor.fetchone()
    active_local_keys = row["cnt"] if row else 0
    cursor = await db.execute("SELECT COUNT(*) as cnt FROM combos WHERE is_active=1")
    row = await cursor.fetchone()
    active_combos = row["cnt"] if row else 0
    return {
        "total_today": total_today,
        "tokens_today": tokens_today,
        "provider_stats": provider_stats,
        "key_stats": key_stats,
        "active_providers": active_providers,
        "active_local_keys": active_local_keys,
        "active_combos": active_combos,
    }


async def get_setting(key: str):
    db = await get_db()
    cursor = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = await cursor.fetchone()
    return row["value"] if row else None


async def set_setting(key: str, value: str):
    db = await get_db()
    await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, value))
    await db.commit()


async def get_all_settings():
    db = await get_db()
    cursor = await db.execute("SELECT key, value FROM settings WHERE key NOT LIKE '%_rr_%' AND key != 'rr_counter'")
    return {r["key"]: r["value"] for r in await cursor.fetchall()}


def datetime_utc_now():
    from datetime import datetime
    return datetime.utcnow().isoformat()
