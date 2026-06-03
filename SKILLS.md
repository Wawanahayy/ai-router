# AI Router Agent Guide

This file is a working guide for AI coding agents that edit or review this
repository. Follow it when making changes to AI Router.

Canonical repository: https://github.com/Wawanahayy/ai-router

## Project Summary

AI Router is a self-hosted AI gateway. It exposes:

- OpenAI-compatible chat completions at `/v1/chat/completions`
- Anthropic-compatible messages at `/v1/messages`
- A web dashboard for providers, keys, models, combos, settings, and logs

The default local server endpoint is:

```text
http://localhost:32128
```

## Repository Map

```text
ai_router/config.py              Runtime configuration from environment
ai_router/db.py                  SQLite schema, migrations, settings, logs
ai_router/proxy.py               Provider routing, fallback, request handling
ai_router/server.py              FastAPI app, dashboard API, auth, static files
ai_router/services/streaming.py  Streaming proxy behavior and timeout handling
ai_router/static/                Built dashboard assets served by backend
web/src/                         React dashboard source
web/src/api.js                   Dashboard API client
run.py                           Backend entrypoint
start.sh                         Linux helper script for setup/run/build
import_keys.py                   Helper for importing provider keys
```

## Development Rules

- Keep changes small and scoped to the user's request.
- Preserve OpenAI-compatible and Anthropic-compatible behavior unless the task explicitly changes it.
- Do not hardcode real provider API keys, local API keys, tokens, cookies, or secrets.
- Do not commit `.env`, SQLite databases, logs, generated reports, local virtualenvs, or `node_modules`.
- Prefer existing database helper functions and API patterns before adding new abstractions.
- For streaming changes, be careful with first-byte delays, heartbeat behavior, cancellation, and upstream timeouts.
- For logging/token changes, make sure both streaming and non-streaming requests are accounted for.
- For dashboard changes, update `web/src` first. Rebuild static assets only when needed for release.

## Setup Commands

Use the helper script on Linux:

```bash
bash start.sh setup
bash start.sh run
```

`bash start.sh setup` creates the Python venv, installs backend requirements,
creates `.env`, and attempts to install Claude Code CLI globally with npm when
npm is available.

Manual backend setup:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python run.py
```

Build the dashboard:

```bash
bash start.sh build-web
```

Run dashboard development mode:

```bash
bash start.sh dev-web
```

## Deployment Notes

The default production/local endpoint is `http://localhost:32128`.

For Cloudflare Tunnel deployment:

- Cloudflare Tunnel is optional. Do not suggest it as required for normal local use.
- Ask the user before installing or configuring Cloudflare Tunnel.
- Explain the purpose clearly: it exposes the dashboard/API so it can be opened from another device, another network, or a temporary public URL.
- `cloudflared` must be installed separately before tunnel commands work.
- Link users to the official downloads page: `https://developers.cloudflare.com/tunnel/downloads/`.
- Temporary tunnels use `cloudflared tunnel --url http://localhost:32128`.
- Temporary `trycloudflare.com` URLs can change after `cloudflared` restarts.
- For always-on Linux usage, document two systemd services: one for AI Router and one for `cloudflared`.
- If the dashboard is exposed, require `AI_ROUTER_AUTH=true` and a changed `AI_ROUTER_PASSWORD`.

## Validation Checklist

Before finishing backend changes:

```bash
python -m py_compile ai_router/config.py ai_router/db.py ai_router/proxy.py ai_router/server.py ai_router/services/streaming.py run.py import_keys.py
```

Before finishing dashboard changes:

```bash
cd web
npm run build
```

Before publishing:

```bash
rg -n "nvapi-|sk-|ar-|api[_-]?key|secret|token" . -g "!.env" -g "!web/node_modules/**"
rg -n "old-default-port" . -g "!web/node_modules/**"
```

The old default port should not appear in active documentation or source. The
current default port is `32128`.

## API Behavior Notes

Base URL:

```text
http://localhost:32128
```

Public/client-compatible endpoints:

```text
GET  /v1/health
GET  /v1/models
POST /v1/models
POST /v1/chat/completions
POST /v1/messages
POST /v1/messages/count_tokens
```

OpenAI-compatible clients should call:

```text
POST /v1/chat/completions
Authorization: Bearer ar-your-local-key
```

Anthropic-compatible clients should call:

```text
POST /v1/messages
Authorization: Bearer ar-your-local-key
anthropic-version: 2023-06-01
```

Claude CLI provider:

Claude-compatible direct HTTP upstreams use provider type `anthropic-compatible`.
When the user wants Claude Code CLI as the upstream adapter, use provider type
`claude-cli`. The provider form should collect base URL, upstream API key, and
models just like other providers; models can then be used directly or inside
combos. AI Router runs the local `claude` binary internally and passes provider
settings through environment variables, so users do not need to manage `export`
commands manually. Claude Code CLI is installed by `bash start.sh setup` when
npm is available; if that fails, tell the user to run
`npm install -g @anthropic-ai/claude-code`.

The local `ar-...` key belongs to AI Router. Upstream provider keys are stored
inside AI Router and should never be exposed to clients.

When creating upstream or local API keys, empty labels/names are auto-numbered
as `apikey-1`, `apikey-2`, and so on. Bulk upstream key imports continue the
same sequence for that provider.

Dashboard/admin API endpoints:

```text
GET    /api/health
GET    /api/auth/status
POST   /api/auth/login
POST   /api/auth/logout

GET    /api/providers
POST   /api/providers
GET    /api/providers/presets
GET    /api/providers/{provider_id}
PUT    /api/providers/{provider_id}
DELETE /api/providers/{provider_id}
POST   /api/providers/{provider_id}/test
POST   /api/providers/{provider_id}/test-tools
POST   /api/providers/{provider_id}/fetch-models
GET    /api/providers/{provider_id}/models
DELETE /api/providers/{provider_id}/models

GET    /api/keys
POST   /api/keys
POST   /api/keys/bulk
PUT    /api/keys/{key_id}
DELETE /api/keys/{key_id}
POST   /api/keys/{key_id}/activate
POST   /api/keys/{key_id}/deactivate

GET    /api/local-keys
POST   /api/local-keys
PUT    /api/local-keys/{key_id}
DELETE /api/local-keys/{key_id}
POST   /api/local-keys/{key_id}/toggle

GET    /api/aliases
POST   /api/aliases
POST   /api/aliases/delete
POST   /api/aliases/activate
POST   /api/aliases/deactivate

GET    /api/logs
GET    /api/stats
GET    /api/settings
PUT    /api/settings

GET    /api/combos
POST   /api/combos
GET    /api/combos/{combo_id}
PUT    /api/combos/{combo_id}
DELETE /api/combos/{combo_id}
POST   /api/combos/{combo_id}/models
PUT    /api/combos/{combo_id}/models/{model_id}
DELETE /api/combos/{combo_id}/models/{model_id}
```

The dashboard/admin API is used by `web/src/api.js`. Check that file before
renaming endpoints or changing request/response shapes.

## Streaming Notes

Some upstream models can take a long time before the first token, especially
reasoning models, large prompts, or queued providers. Do not add aggressive
first-byte timeouts without checking how this affects slow but valid responses.

When editing streaming code, verify:

- Slow upstream response handling
- Client disconnect handling
- Fallback attempt logging
- Final usage logging
- OpenAI stream format
- Anthropic stream format

## Database Notes

The app uses SQLite by default. Database files are local runtime state and are
ignored by Git.

Do not include real database files in commits. They may contain:

- Upstream provider API keys
- Local `ar-...` API keys
- Request logs
- Prompt/model usage data
- Dashboard settings

If schema changes are needed, implement migrations in the existing database
initialization flow instead of requiring manual SQL from users.

## Dashboard Notes

The dashboard source lives in `web/src`. Built files live in
`ai_router/static`.

Important dashboard pages:

- `Dashboard.jsx`: status, endpoint info, provider usage
- `Providers.jsx`: provider CRUD, model management
- `Keys.jsx`: upstream provider keys
- `LocalKeys.jsx`: local client API keys
- `Combos.jsx`: combo routing
- `Logs.jsx`: request logs
- `Settings.jsx`: auth, aliases, connection info

Keep endpoint text consistent with port `32128` and both supported formats:

- OpenAI: `/v1/chat/completions`
- Anthropic: `/v1/messages`

## Security Expectations

When preparing this repository for open source:

- Confirm `.env` is ignored.
- Confirm `data/` and database extensions are ignored.
- Confirm unrelated local output folders such as `free-credits/` are ignored.
- Confirm no real API keys are present in source, docs, or committed data.
- Keep default password documentation clear, but tell users to change it before public exposure.

## Response Style for Agents

When reporting changes to a human maintainer:

- Mention changed files.
- Mention what was verified.
- Mention anything not verified and why.
- Keep the answer direct and actionable.
