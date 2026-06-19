#!/usr/bin/env python3
import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "http://localhost:32128"


def request_json(base_url, path, method="GET", data=None, token=None):
    url = base_url.rstrip("/") + path
    body = None
    headers = {"Accept": "application/json"}
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}: {raw}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Connection failed: {exc.reason}")


def mask(value):
    value = value or ""
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:6]}...{value[-4:]}"


def print_provider_keys(keys):
    if not keys:
        print("No provider API keys found.")
        return
    for idx, item in enumerate(keys, 1):
        lock = item.get("lock") or {}
        lock_text = f" lock={lock.get('model')}:{lock.get('remaining')}s" if lock else ""
        print(
            f"{idx}. id={item.get('id')} provider={item.get('provider_name') or item.get('provider_id')} "
            f"label={item.get('label') or '-'} status={item.get('status')} key={mask(item.get('key_value'))}{lock_text}"
        )


def print_local_keys(keys):
    if not keys:
        print("No local API keys found.")
        return
    for idx, item in enumerate(keys, 1):
        active = "active" if item.get("is_active") else "disabled"
        print(
            f"{idx}. id={item.get('id')} name={item.get('name') or '-'} status={active} "
            f"key={mask(item.get('key_value'))} rate_limit={item.get('rate_limit') or 0}"
        )


def pick_id(items, explicit_id):
    if explicit_id:
        return explicit_id
    if not items:
        raise SystemExit("No keys available.")
    choice = input("Pick number or paste key id: ").strip()
    if not choice:
        raise SystemExit("Cancelled.")
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(items):
            return items[idx - 1]["id"]
    return choice


def prompt_value(label, current=None, secret=False):
    suffix = f" [{current}]" if current and not secret else ""
    prompt = f"{label}{suffix}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    return value if value else None


def build_provider_update(args, selected):
    data = {}
    if args.label is not None:
        data["label"] = args.label
    if args.key is not None:
        data["key"] = args.key
    if args.status is not None:
        data["status"] = args.status
    if args.interactive:
        label = prompt_value("New label", selected.get("label"))
        key = prompt_value("New upstream API key", secret=True)
        status = prompt_value("Status alive/cooldown/dead", selected.get("status"))
        if label is not None:
            data["label"] = label
        if key is not None:
            data["key"] = key
        if status is not None:
            data["status"] = status
    return data


def build_local_update(args, selected):
    data = {}
    if args.name is not None:
        data["name"] = args.name
    if args.key is not None:
        data["key_value"] = args.key
    if args.active is not None:
        data["is_active"] = 1 if args.active else 0
    if args.rate_limit is not None:
        data["rate_limit"] = args.rate_limit
    if args.interactive:
        name = prompt_value("New name", selected.get("name"))
        key = prompt_value("New local API key", secret=True)
        active = prompt_value("Active 1/0", str(int(bool(selected.get("is_active")))))
        rate_limit = prompt_value("Rate limit", str(selected.get("rate_limit") or 0))
        if name is not None:
            data["name"] = name
        if key is not None:
            data["key_value"] = key
        if active is not None:
            data["is_active"] = 1 if active.lower() in ("1", "true", "yes", "y", "on") else 0
        if rate_limit is not None:
            data["rate_limit"] = int(rate_limit)
    return data


def find_selected(items, key_id):
    for item in items:
        if item.get("id") == key_id:
            return item
    return {}


def main():
    parser = argparse.ArgumentParser(description="Edit ai-router API keys through localhost:32128.")
    parser.add_argument("--base-url", default=os.getenv("AI_ROUTER_URL", DEFAULT_BASE_URL))
    parser.add_argument("--auth", default=os.getenv("AI_ROUTER_ADMIN_TOKEN", ""))
    parser.add_argument("--local", action="store_true", help="Edit local ai-router client keys instead of provider upstream keys.")
    parser.add_argument("--provider-id", help="Filter provider keys by provider id.")
    parser.add_argument("--id", dest="key_id", help="Key id to edit.")
    parser.add_argument("--list", action="store_true", help="Only list keys.")
    parser.add_argument("--interactive", "-i", action="store_true", help="Prompt for values interactively.")
    parser.add_argument("--key", help="New API key value.")
    parser.add_argument("--label", help="New provider key label.")
    parser.add_argument("--status", choices=["alive", "cooldown", "dead"], help="New provider key status.")
    parser.add_argument("--name", help="New local key name.")
    parser.add_argument("--active", type=int, choices=[0, 1], help="Set local key active state.")
    parser.add_argument("--rate-limit", type=int, help="Set local key rate_limit.")
    args = parser.parse_args()

    if args.local:
        items = request_json(args.base_url, "/api/local-keys", token=args.auth) or []
        print_local_keys(items)
        if args.list:
            return
        key_id = pick_id(items, args.key_id)
        selected = find_selected(items, key_id)
        data = build_local_update(args, selected)
        if not data:
            raise SystemExit("Nothing to update. Use --interactive or pass --key/--name/--active/--rate-limit.")
        result = request_json(args.base_url, f"/api/local-keys/{urllib.parse.quote(key_id)}", "PUT", data, args.auth)
        print(json.dumps(result or {"ok": True}, indent=2))
        return

    query = ""
    if args.provider_id:
        query = "?" + urllib.parse.urlencode({"provider_id": args.provider_id})
    items = request_json(args.base_url, f"/api/keys{query}", token=args.auth) or []
    print_provider_keys(items)
    if args.list:
        return
    key_id = pick_id(items, args.key_id)
    selected = find_selected(items, key_id)
    data = build_provider_update(args, selected)
    if not data:
        raise SystemExit("Nothing to update. Use --interactive or pass --key/--label/--status.")
    result = request_json(args.base_url, f"/api/keys/{urllib.parse.quote(key_id)}", "PUT", data, args.auth)
    print(json.dumps(result or {"ok": True}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
