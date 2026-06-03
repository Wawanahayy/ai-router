#!/usr/bin/env python3
"""Import API keys into an AI Router provider.

Run `python import_keys.py --help` for usage details.
"""


import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


PROVIDER_PRESETS = {
    "novai": {"name": "NovAI", "type": "openai-compatible", "base_url": "https://aiapi-pro.com/v1", "prefix": "novai", "prefix_enabled": 0},
    "openai": {"name": "OpenAI", "type": "openai-compatible", "base_url": "https://api.openai.com/v1", "prefix": "openai", "prefix_enabled": 0},
    "anthropic": {"name": "Anthropic", "type": "anthropic-compatible", "base_url": "https://api.anthropic.com/v1", "prefix": "anthropic", "prefix_enabled": 0, "auth_type": "x-api-key", "chat_path": "/messages"},
    "claude-cli": {"name": "Claude CLI Provider", "type": "claude-cli", "base_url": "https://api.anthropic.com/v1", "prefix": "claude-cli", "prefix_enabled": 0, "auth_type": "x-api-key", "chat_path": "/messages", "request_format": "anthropic-compatible", "supports_tools": 0, "supports_streaming": 0},
    "nvidia": {"name": "Nvidia NIM", "type": "openai-compatible", "base_url": "https://integrate.api.nvidia.com/v1", "prefix": "nvidia", "prefix_enabled": 0},
    "openrouter": {"name": "OpenRouter", "type": "openai-compatible", "base_url": "https://openrouter.ai/api/v1", "prefix": "openrouter", "prefix_enabled": 0},
    "groq": {"name": "Groq", "type": "openai-compatible", "base_url": "https://api.groq.com/openai/v1", "prefix": "groq", "prefix_enabled": 0},
    "deepseek": {"name": "DeepSeek", "type": "openai-compatible", "base_url": "https://api.deepseek.com/v1", "prefix": "deepseek", "prefix_enabled": 0},
    "together": {"name": "Together AI", "type": "openai-compatible", "base_url": "https://api.together.xyz/v1", "prefix": "together", "prefix_enabled": 0},
    "fireworks": {"name": "Fireworks AI", "type": "openai-compatible", "base_url": "https://api.fireworks.ai/inference/v1", "prefix": "fireworks", "prefix_enabled": 0},
}


def api_call(base_url, path, method="GET", data=None, password=None):
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Content-Type": "application/json"}
    if password:
        headers["Authorization"] = f"Bearer {password}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            text = resp.read().decode()
            return json.loads(text) if text else {}
    except urllib.error.HTTPError as e:
        print(f"  ERROR: {e.code} {e.read().decode()[:300]}")
        return None


def normalize(value):
    return (value or "").strip().lower()


def find_provider(providers, wanted):
    wanted_norm = normalize(wanted)
    for provider in providers:
        names = {
            normalize(provider.get("id")),
            normalize(provider.get("name")),
            normalize(provider.get("prefix")),
        }
        if wanted_norm in names:
            return provider
    return None


def provider_payload(args):
    preset = PROVIDER_PRESETS.get(normalize(args.provider))
    if preset:
        data = dict(preset)
    else:
        data = {
            "name": args.provider,
            "type": args.type,
            "base_url": args.base_url,
            "prefix": args.prefix or normalize(args.provider).replace(" ", "-"),
            "prefix_enabled": 0,
        }

    if args.name:
        data["name"] = args.name
    if args.type:
        data["type"] = args.type
    if args.base_url:
        data["base_url"] = args.base_url
    if args.prefix:
        data["prefix"] = args.prefix
    for field in ("auth_type", "auth_header", "auth_prefix", "key_query_param", "chat_path", "models_path", "request_format"):
        value = getattr(args, field)
        if value:
            data[field] = value
    return data


def read_key_file(path):
    keys = []
    if not os.path.exists(path):
        return keys

    with open(path, "r", encoding="utf-8") as f:
        for index, raw in enumerate(f):
            line = raw.strip()
            if not line:
                continue
            label = f"{os.path.basename(path)}:{index + 1}"
            key = line
            if line.startswith("{"):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    obj = {}
                key = obj.get("key") or obj.get("api_key") or obj.get("apiKey") or obj.get("token") or ""
                label = obj.get("label") or obj.get("email") or obj.get("name") or label
            if key and len(key) > 10:
                keys.append({"key": key, "label": label})
    return keys


def unique_keys(keys):
    seen = set()
    result = []
    for item in keys:
        key = item["key"]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def main():
    parser = argparse.ArgumentParser(description="Import API keys into any ai-router provider.")
    parser.add_argument("--api", default=os.getenv("AI_ROUTER_API", "http://localhost:32128"))
    parser.add_argument("--password", default=os.getenv("AI_ROUTER_PASSWORD", ""))
    parser.add_argument("--provider", default=os.getenv("AI_ROUTER_IMPORT_PROVIDER", ""), help="Provider name/id/prefix, e.g. novai, nvidia, openrouter.")
    parser.add_argument("--name", default="", help="Provider display name when creating a custom provider.")
    parser.add_argument("--type", default="openai-compatible", choices=["openai-compatible", "anthropic-compatible", "claude-cli"])
    parser.add_argument("--base-url", default="", help="Required for custom providers not in presets.")
    parser.add_argument("--prefix", default="", help="Provider prefix when creating/updating provider payload.")
    parser.add_argument("--auth-type", default="", choices=["", "bearer", "x-api-key", "api-key", "header", "query", "none"], help="How to send the provider key.")
    parser.add_argument("--auth-header", default="", help="Header name for custom/header auth, e.g. X-API-Key.")
    parser.add_argument("--auth-prefix", default="", help="Prefix before key in header auth, e.g. 'Bearer '.")
    parser.add_argument("--key-query-param", default="", help="Query parameter name when --auth-type=query.")
    parser.add_argument("--chat-path", default="", help="Override chat endpoint path, e.g. /chat/completions.")
    parser.add_argument("--models-path", default="", help="Override models endpoint path, e.g. /models.")
    parser.add_argument("--request-format", default="", choices=["", "openai-compatible", "anthropic-compatible"], help="Request/response translation format.")
    parser.add_argument("--file", action="append", default=[], help="Key file to import. Can be used multiple times.")
    parser.add_argument("--accounts", default="", help="Optional JSONL file with api_key/key fields.")
    parser.add_argument("--apikeys", default="", help="Optional plain text key file, one key per line.")
    parser.add_argument("--create-presets", action="store_true", help="Create all built-in provider presets before importing.")
    args = parser.parse_args()

    providers = api_call(args.api, "/api/providers", password=args.password)
    if providers is None:
        print("Failed to read providers. If auth is enabled, pass --password or set AI_ROUTER_PASSWORD.")
        return 1

    if args.create_presets:
        existing = {normalize(p.get("name")) for p in providers}
        for preset in PROVIDER_PRESETS.values():
            if normalize(preset["name"]) not in existing:
                created = api_call(args.api, "/api/providers", "POST", preset, password=args.password)
                if created:
                    print(f"  Created provider: {preset['name']}")
        providers = api_call(args.api, "/api/providers", password=args.password) or []

    if not args.provider:
        print("Choose a target provider with --provider. Existing providers:")
        for provider in providers:
            print(f"  - {provider.get('name')} (id={provider.get('id')}, prefix={provider.get('prefix')})")
        return 1

    provider = find_provider(providers, args.provider)
    if not provider:
        payload = provider_payload(args)
        if not payload.get("base_url"):
            print("Provider not found and --base-url is required for custom provider creation.")
            return 1
        provider = api_call(args.api, "/api/providers", "POST", payload, password=args.password)
        if not provider:
            print(f"Failed to create provider: {payload.get('name')}")
            return 1
        print(f"  Created provider: {provider.get('name', payload.get('name'))}")

    files = list(args.file)
    if args.accounts:
        files.append(args.accounts)
    if args.apikeys:
        files.append(args.apikeys)
    if not files:
        print("No key files found. Pass --file, --accounts, or --apikeys.")
        return 1

    keys = []
    for path in files:
        file_keys = read_key_file(path)
        print(f"  Read {len(file_keys)} key(s) from {path}")
        keys.extend(file_keys)
    keys = unique_keys(keys)
    if not keys:
        print("No valid keys found.")
        return 1

    result = api_call(args.api, "/api/keys/bulk", "POST", {
        "provider_id": provider["id"],
        "keys": keys,
    }, password=args.password)
    if not result:
        return 1

    print(f"\n=== Import Complete ===")
    print(f"Provider: {provider.get('name')} ({provider.get('id')})")
    print(f"Imported: {result.get('added', 0)} key(s)")
    stats = api_call(args.api, "/api/stats", password=args.password) or {"key_stats": {}}
    print(f"Keys total: alive={stats['key_stats'].get('alive',0)} dead={stats['key_stats'].get('dead',0)} cooldown={stats['key_stats'].get('cooldown',0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
